#!/usr/bin/env python3

# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

import os, sys, dbm, logging, signal, hashlib
import contextlib, functools, configparser
import mailbox, email.policy, urllib.parse
import imapclient

# register a signal handler for clean(er) exits
def interrupt(sig, frame):
    print(" interrupt.")
    sys.exit(0)
signal.signal(signal.SIGINT, interrupt)

# Mailserver is a connection helper to an IMAP4 server.
#! Since most IMAP operations are performed on the currently selected folder
#! this is absolutely not safe for concurrent use. Weird things *will* happen.
class Mailserver:

    def __init__(self, host, username, password, logger=None):
        logname = "{}/mailserver".format(logger.name if logger is not None else "imapfetch")
        self.log = logging.getLogger(logname)
        self.log.info("connecting to {}".format(host))
        self.client = imapclient.IMAPClient(host=host, use_uid=True, ssl=True)
        self.log.info("logging in as {}".format(username))
        self.client.login(username, password)
        
    # stubs for use as a context manager
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, tb):
        try: self.client.logout()
        except: pass

    # list available folders
    def ls(self, directory="", pattern="*"):
        return [f[2] for f in self.client.list_folders(directory, pattern)]

    # select a folder, always readonly
    def cd(self, folder):
        return self.client.select_folder(folder, readonly=True)

    # get new mail uids in current folder, starting with uid start
    def mails(self, start=1):
        return self.client.search("UID {}:*". format(start))

    # chunk sizes for partial fetches, first flight and remaining chunks
    # a sufficiently large firstflight chunk can fetch messages in one go
    FIRSTFLIGHT =      64*1024 # 64 KB
    NEXTCHUNKS  = 10*1024*1024 # 10 MB

    # commonly useful data selectors for fetch
    # https://tools.ietf.org/html/rfc3501#section-6.4.5
    SIZE, HEADER, FULL = b"RFC822.SIZE", b"BODY[HEADER]", b"BODY[]"
    TEXTF, TEXT = b"BODY[TEXT]<%d.%d>", b"BODY[TEXT]<%d>"

    # thin wrapper on imapclient's fetch for debug logging
    def fetch(self, uid, data, modifiers=None):
        self.log.debug("FETCH {} [{}]".format(uid, data))
        return self.client.fetch(uid, data, modifiers)[uid]

    # retrieve a specific message by uid; return header and body generator
    def message(self, uid, firstflight=FIRSTFLIGHT, chunk=NEXTCHUNKS):

        # fetch message header, size and "firstflight" chunk
        msg = self.fetch(uid, [self.SIZE, self.HEADER, self.TEXTF % (0, firstflight)])
        size, header, text = msg[self.SIZE], msg[self.HEADER], msg[self.TEXT % (0)]

        # function to dynamically fetch and yield message parts as necessary
        def generator():
            nonlocal text
            pos = len(text)
            yield header + text
            while size > (len(header) + pos):
                self.log.debug("next partial: <{}.{}>".format(pos, chunk))
                part = self.fetch(uid, [self.TEXTF % (pos, chunk)])[self.TEXT % (pos)]
                pos += len(part)
                yield part

        return header, size, generator


#! MIGRATION NOTES
# If you're trying to move from a previous version, you'll likely need some
# manual migrations, specifically re-reading all messages to recreate the index?
#
#   - The message.as_bytes() returns a different header than what the IMAP server
#     returns for fetches of BODY[HEADER]. In my testing, setting the policy as
#     message.policy = email.policy.HTTP results in identical headers, which is
#     important to get identical hashes for the index ... I'm not sure yet if
#     parsing _every_ message through MaildirMessage is a good idea for local
#     canonicalization? It seems that at least Windows has some problems with
#     the output format resulting from this step? Certainly, directly writing
#     the server response for BODY[] to disk is more performant ...

# Maildir subclasses mailbox.Maildir to change storage and filename format
# partly adapted from https://github.com/python/cpython/blob/master/Lib/mailbox.py
class Maildir(mailbox.Maildir):
    colon = "!" # should never be needed

    # greatly simplified file writer that uses content-addressable
    # filenames through a digest of the raw message header
    def add(self, message, uid=0):
        if not isinstance(message, Message):
            raise TypeError("message must be a Message object")
        # hash header to get content-addressable filename
        name = message.uniqname(uid)
        path = os.path.join(self._path, "cur", name)
        try: os.stat(path)
        except FileNotFoundError:
            file = mailbox._create_carefully(path)
            file.write(message.as_bytes())
            file.close()
            return name
        raise FileExistsError("a message with this header digest exists: {}".format(name))



# Message is a wrapper around MaildirMessage with necessary properties
# applied to normalize it and compute stable header digests.
class Message(mailbox.MaildirMessage):
    def __init__(self, message):
        super().__init__(message)
        self._digest = self._header = None
        # apply policy to use \r\n and long lines
        self.policy = email.policy.HTTP

    # slice and cache the header as bytes
    def header(self):
        if not self._header:
            message = self.as_bytes()
            self._header = message[:message.index(b"\r\n\r\n")+4]
        return self._header

    # compute and cache the header digest
    def digest(self):
        if not self._digest:
            self._digest = hashlib.sha224(self.header()).digest()
        return self._digest

    # return a filename for storage
    def uniqname(self, uid=0):
        return "{:010d}-{}.eml".format(uid, self.digest().hex())



# Archive is a maildir-based email storage for local on-disk archival.
# It opens a directory and creates several mailboxes (one per inbox) and
# a small indexing database within.
class Archive:

    # open a new archive
    def __init__(self, path, logger=None, quoting=False):
        logname = "{}/archive".format(logger.name if logger is not None else "imapfetch")
        self.log = logging.getLogger(logname)
        self.path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(self.path, exist_ok=True)
        self.index = dbm.open(os.path.join(self.path, "index"), flag="c")
        self.log.info("opened archive in {}".format(self.path))
        self.quoting = quoting

    # stubs for use as a context manager
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, tb):
        try: self.index.close()
        except: pass

    # store highest-seen uid per inbox
    def lastseen(self, inbox, uid=None):
        if uid is None: return int(self.index.get("uid/{}".format(inbox), 1))
        else: self.index["uid/{}".format(inbox)] = str(uid)

    # check if a message is already in the archive by checking header digest
    def __contains__(self, message):
        if not isinstance(message, Message):
            message = Message(message)
        return message.digest() in self.index

    # return a maildir instance for an inbox folder
    @functools.lru_cache(maxsize=8)
    def inbox(self, folder):
        # quote a folder name with urlencode to make it safe(r)
        if self.quoting:
            folder = urllib.parse.quote_plus(folder)
        folder = folder.replace("/", ".")
        return Maildir(os.path.join(self.path, folder), create=True)

    # archive a message in mailbox
    def store(self, folder, message, uid=0):
        if not isinstance(message, Message):
            message = Message(message)
        if message in self:
            raise FileExistsError("message already in index")
        inbox = self.inbox(folder)
        file = inbox.add(message, uid)
        self.index[message.digest()] = uid.to_bytes(4, "big")
        self.log.warning("message uid {} stored in {}".format(uid, file))
        return message.digest()



# Account is a configuration helper, parsing a section from a configuration file
# and for use in a with..as.. statement.
class Account:
    # parse account data from configuration section
    def __init__(self, section, logger=None):
        self.logger = logger
        self.path = section.get("archive")
        self.exclude = section.get("exclude", "").strip().split("\n")
        self.server = section.get("server")
        self.username = section.get("username")
        self.password = section.get("password")
        self.quoting = section.get("quoting", False)

    # yield a mailserver connection from credentials
    @contextlib.contextmanager
    def imap(self):
        with Mailserver(self.server, self.username, self.password, self.logger) as ms:
            yield ms

    # yield an archive instance at path
    @contextlib.contextmanager
    def archive(self):
        with Archive(self.path, self.logger, self.quoting) as ar:
            yield ar

    # contextmanager wrapping both of the above
    @contextlib.contextmanager
    def ctx(self):
        with self.imap() as ms, self.archive() as ar:
            yield self, ms, ar



# -----------------------------------------------------------------------------------
def imapfetch():

    import argparse
    import fnmatch

    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="configuration file", type=argparse.FileType("r"))
    parser.add_argument("section", help="sections to execute", nargs="*")
    parser.add_argument("--full", "-f", help="do full backups", action="store_true")
    parser.add_argument("--list", "-l", help="only list folders", action="store_true")
    parser.add_argument("--verbose", "-v", help="increase logging verbosity", action="count", default=0)
    args = parser.parse_args()

    # configure logging format and verbosity
    # TODO: add more level between info and debug again?
    level = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level = level[min(len(level) - 1, args.verbose)]
    logging.basicConfig(format="%(name)s: %(message)s", level=level)
    log = logging.getLogger("imapfetch")
    log.debug(args)

    # read configuration
    log.info("read configuration from {}".format(args.config.name))
    conf = configparser.ConfigParser()
    conf.read_file(args.config)

    # check given section names for existence
    for section in args.section:
        if section not in conf.sections():
            raise ValueError("no such section in configuration: {}".format(section))

    # iterate over selected configuration sections
    for section in (args.section or conf.sections()):

        # create logger
        log.info("processing section {}".format(section))
        sectlog = logging.getLogger(section)

        # if --list is given only connect and show folders
        if args.list:
            with Account(conf[section], sectlog).imap() as mailserver:
                sectlog.info("listing folders:")
                for folder in mailserver.ls():
                    sectlog.warning(folder)
            continue

        # otherwise open archive for processing
        with Account(conf[section], sectlog).ctx() as (acc, mailserver, archive):
            for folder in mailserver.ls():

                # test for exclusion matches
                def checkskip(rules, folder):
                    for pattern in rules:
                        if fnmatch.fnmatch(folder, pattern):
                            sectlog.info("excluded folder {} due to {!r}".format(folder, pattern))
                            return True
                if checkskip(acc.exclude, folder):
                    continue

                # send imap command to change directory 
                sectlog.warning("processing folder {}".format(folder))
                mailserver.cd(folder)

                # retrieve the highest known uid from index
                uidkey = "{}/{}".format(section, folder)
                highest = archive.lastseen(uidkey)
                sectlog.debug("lastseen uid for {} = {}".format(uidkey, highest))
                if args.full:
                    sectlog.info("starting at uid 1")

                # iterate over all uids >= highest
                for uid in mailserver.mails(1 if args.full else highest):
                    header, size, generator = mailserver.message(uid)
                    
                    # check if the email is stored already
                    if header in archive:
                        sectlog.info("message uid {} stored already".format(uid))
                    else:
                        # otherwise collect full message and store
                        message = b"".join(generator())
                        archive.store(folder, message, uid)

                    # store highest seen uid per folder
                    if uid > highest:
                        archive.lastseen(uidkey, uid)
                        highest = uid


if __name__ == "__main__":
    imapfetch()
