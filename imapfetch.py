#!/usr/bin/env python3

"""Download emails from an IMAP mailserver and store them in a maildir format."""
# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

__version__ = "1.0.0"

import os, sys, logging, signal, hashlib, sqlite3
import contextlib, functools, urllib.parse
import mailbox, email.policy
import imapclient

# register a signal handler for clean(er) exits
def interrupt(sig, frame):
    print(" interrupt.")
    sys.exit(0)
signal.signal(signal.SIGINT, interrupt)

# add a verbose logging level and use keywords
from logging import ERROR, WARNING, INFO, DEBUG
VERBOSE = INFO - 5

# Mailserver is a connection helper to an IMAP4 server.
#! Since most IMAP operations are performed on the currently selected folder
#! this is absolutely not safe for concurrent use. Weird things *will* happen.
class Mailserver:

    def __init__(self, host, username, password, logger=None):
        self.log = logger.log if logger else logging.getLogger("mailserver").log
        self.log(INFO, "connecting to {}".format(host))
        self.client = imapclient.IMAPClient(host=host, use_uid=True, ssl=True)
        self.log(INFO, "logging in as {}".format(username))
        self.client.login(username, password)
        if b"Microsoft Exchange" in self.client.welcome:
          self.compat = True
          self.log(INFO, "this is an exchange server")
        if b"OK Gimap ready for requests" in self.client.welcome:
          self.compat = True
          self.log(INFO, "this is a Gmail server")

    # is this an exchange or gmail server?
    compat = False

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
    # partial fetch and key to retrieve from response dict
    TEXTF, TEXT = b"BODY[TEXT]<%d.%d>", b"BODY[TEXT]<%d>"

    # thin wrapper on imapclient's fetch for debug logging
    def fetch(self, uid, data, modifiers=None):
        self.log(VERBOSE, "FETCH {} [{}]".format(uid, data))
        return self.client.fetch(uid, data, modifiers)[uid]

    # retrieve a specific message by uid; return header and body generator
    def message(self, uid, firstflight=FIRSTFLIGHT, chunk=NEXTCHUNKS):

        # fetch message header, size and "firstflight" chunk
        msg = self.fetch(uid, [self.SIZE, self.HEADER, self.TEXTF % (0, firstflight)])
        size, header, text = msg[self.SIZE], msg[self.HEADER], msg[self.TEXT % (0)]

        # function to dynamically fetch and yield message parts as necessary
        def generator():
            nonlocal text
            # sometimes, the text may be None when the body is empty; this is confusing
            if self.compat and text is None:
                self.log(VERBOSE, "compat: received an empty message")
                yield header
                return
            pos = len(text)
            yield header + text
            while size > (len(header) + pos):
                self.log(VERBOSE, "next partial: <{}.{}>".format(pos, chunk))
                part = self.fetch(uid, [self.TEXTF % (pos, chunk)])[self.TEXT % (pos)]
                # exchange+gmail report unreliable size and may return None body early
                if self.compat and (part is None or len(part) == 0):
                    self.log(VERBOSE, "compat: premature end of message, wrong size reported")
                    return
                pos += len(part)
                yield part

        return header, size, generator


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
        self.log = logger.log if logger else logging.getLogger("archive").log
        self.path = os.path.abspath(os.path.expanduser(path))
        self.__check_oldversion()
        os.makedirs(self.path, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(self.path, "index.db"))
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id          INTEGER     PRIMARY KEY,
            folder      TEXT        UNIQUE NOT NULL,
            lastseen    INTEGER
        )""")
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            digest      BLOB        PRIMARY KEY,
            folder      INTEGER     NOT NULL,
            uid         INTEGER     NOT NULL,
            FOREIGN KEY (folder) REFERENCES folders (id)
        )""")
        self.db.commit()
        self.log(INFO, "opened archive in {}".format(self.path))
        self.quoting = quoting

    # check if this archive was created with a previous version
    def __check_oldversion(self):
        if os.path.isfile(os.path.join(self.path, "index")):
            self.log(INFO, "\"index\" file found in archive directory")
            self.log(ERROR, "archive was created with a previous version")
            raise AssertionError("incompatible archive format")

    # stubs for use as a context manager
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, tb):
        try:
            self.db.commit()
            self.db.close()
        except: pass

    # store highest-seen uid per folder
    def lastseen(self, folder, uid=None):
        if uid is None: # retrieve uid
            result = self.db.execute("SELECT lastseen FROM folders WHERE folder = ?", (folder,)).fetchone()
            return result[0] or 1 if result else 1
        else: # otherwise store as lastseen
            self.db.execute("""INSERT INTO folders (folder, lastseen) VALUES (?, ?)
                ON CONFLICT (folder) DO UPDATE SET lastseen = excluded.lastseen""", (folder, uid))

    # check if a message is already in the archive by checking header digest
    def __contains__(self, message):
        if not isinstance(message, Message):
            message = Message(message)
        return self.db.execute("SELECT 1 FROM messages WHERE digest = ?", (message.digest(),)).fetchone() != None

    # return a maildir instance for an inbox folder
    @functools.lru_cache(maxsize=8)
    def inbox(self, folder):
        # quote a folder name with urlencode to make it safe(r)
        if self.quoting:
            folder = urllib.parse.quote_plus(folder)
        folder = folder.replace("/", ".")
        self.log(VERBOSE, "open mailbox in {!r}".format(folder))
        return Maildir(os.path.join(self.path, folder), create=True)

    # archive a message in mailbox
    def store(self, folder, message, uid=0):
        if not isinstance(message, Message):
            message = Message(message)
        if message in self:
            raise FileExistsError("message already in index")
        inbox = self.inbox(folder)
        file = inbox.add(message, uid)
        self.lastseen(folder, uid)
        self.db.execute("""INSERT INTO messages (digest, folder, uid) VALUES
            (?, (SELECT id FROM folders WHERE folder = ?), ?)""", (message.digest(), folder, uid))
        self.db.commit()
        self.log(INFO, "message uid {} stored in {}".format(uid, file))
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
def commandline():

    import argparse, configparser, fnmatch
    parser = argparse.ArgumentParser(description="imapfetch {}".format(__version__))
    parser.add_argument("config", help="configuration file", type=argparse.FileType("r"))
    parser.add_argument("section", help="sections to execute", nargs="*")
    parser.add_argument("--full", "-f", help="do full backups", action="store_true")
    parser.add_argument("--list", "-l", help="only list folders", action="store_true")
    parser.add_argument("--verbose", "-v", help="increase logging verbosity", action="count", default=1)
    args = parser.parse_args()

    # configure logging format and verbosity
    level = [ERROR, WARNING, INFO, VERBOSE, DEBUG]
    level = level[min(len(level) - 1, args.verbose)]
    logging.basicConfig(format="%(name)s: %(message)s", level=level)
    applog = logging.getLogger("imapfetch").log
    applog(DEBUG, args)

    # read configuration
    applog(INFO, "read configuration from {}".format(args.config.name))
    conf = configparser.ConfigParser()
    conf.read_file(args.config)

    # check given section names for existence
    for section in args.section:
        if section not in conf.sections():
            applog(ERROR, "no such section in configuration: {}".format(section))
            sys.exit(1)

    # iterate over selected configuration sections
    errors = { }
    for section in (args.section or conf.sections()):

        # create logger
        applog(INFO, "processing section {}".format(section))
        logger = logging.getLogger(section)
        log = logger.log

        try:

            # if --list is given only connect and show folders
            if args.list:
                with Account(conf[section], logger).imap() as mailserver:
                    log(INFO, "listing folders:")
                    for folder in mailserver.ls():
                        log(WARNING, folder)
                continue

            # otherwise open archive for processing
            with Account(conf[section], logger).ctx() as (acc, mailserver, archive):
                for folder in mailserver.ls():

                    # test for exclusion matches
                    def checkskip(rules, folder):
                        for pattern in rules:
                            if fnmatch.fnmatch(folder, pattern):
                                log(VERBOSE, "excluded folder {} due to {!r}".format(folder, pattern))
                                return True
                    if checkskip(acc.exclude, folder):
                        continue

                    # send imap command to change directory 
                    log(INFO, "processing folder {}".format(folder))
                    mailserver.cd(folder)

                    # retrieve the highest known uid from index
                    highest = archive.lastseen(folder)
                    log(VERBOSE, "lastseen uid for {} = {}".format(folder, highest))
                    if args.full:
                        log(INFO, "starting at uid 1")

                    # iterate over all uids >= highest
                    for uid in mailserver.mails(1 if args.full else highest):
                        header, size, generator = mailserver.message(uid)
                        
                        # check if the email is stored already
                        if header in archive:
                            log(VERBOSE, "message uid {} stored already".format(uid))
                            archive.lastseen(folder, uid)
                        else:
                            # otherwise collect full message and store
                            message = b"".join(generator())
                            archive.store(folder, message, uid)

        except Exception as err:
            errors[section] = err
            logger.exception(err)
        
    if len(errors.keys()):
        applog(ERROR, "encountered errors!")
        for section, err in errors.items():
            applog(ERROR, "{}: {!r}".format(section, err))
        sys.exit(1)


if __name__ == "__main__":
    commandline()
