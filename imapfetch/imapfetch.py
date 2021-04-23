#!/usr/bin/env python3

# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

import imapclient
import functools
import mailbox, email.policy
import logging
import configparser
import time
import binascii
import dbm
import email
import os
import re

# import hashlib from library or pyblake2 package
try:
  from hashlib import blake2b
except ImportError:
  from pyblake2 import blake2b

# join path to absolute and expand ~ home
pjoin = lambda arr: os.path.abspath(os.path.join(*[os.path.expanduser(p) for p in arr]))


# Mailserver is a connection helper to an IMAP4 server.
#! Since most IMAP operations are performed on the currently selected folder
#! this is absolutely not safe for concurrent use. Weird things *will* happen.
class Mailserver:

    def __init__(self, host, username, password, logger=None):
        self.log = logger or logging.getLogger("mailserver/{}".format(host))
        self.log.info("connecting to {}".format(host))
        self.client = imapclient.IMAPClient(host=host, use_uid=True, ssl=True)
        self.log.info("logging in as {}".format(username))
        self.client.login(username, password)
        
    # stubs for use as a context manager
    def __enter__(self):
        return self
    def __exit__(self):
        self.client.logout()

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
        print(f"debug: FETCH {uid} [{data}]") # TODO: logger
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
                print(f"fetch next partial from <{pos}.{chunk}>") # TODO: logger
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
    def add(self, message):
        if not isinstance(message, Message):
            raise TypeError("message must be a Message object")
        # hash header to get content-addressable filename
        name = message.uniqname()
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

    # return and cache the header in bytes
    def header(self):
        if not self._header:
            message = self.as_bytes()
            self._header = message[:message.index(b"\r\n\r\n")+4]
        return self._header

    # return and cache the header digest
    def digest(self):
        if not self._digest:
            self._digest = blake2b(self.header(), digest_size=32).digest()
        return self._digest

    # return a filename for storage
    def uniqname(self):
        return "{}.eml".format(self.digest().hex())



# Archive is a maildir-based email storage for local on-disk archival.
# It opens a directory and creates several mailboxes (one per inbox) and
# a small indexing database within.
class Archive:

    # open a new archive
    def __init__(self, path, logger=None, quoting=False):
        self.log = logger.getChild("archive") if logger is not None else logging.getLogger("archive")
        self.path = pjoin([path])
        os.makedirs(self.path, exist_ok=True)
        self.index = dbm.open(pjoin([self.path, "index"]), flag="c")
        self.log.info("opened archive in {}".format(self.path))

    # stubs for use as a context manager
    def __enter__(self):
        return self
    def __exit__(self):
        self.index.close()

    # store highest-seen uid per inbox
    def lastseen(self, inbox, uid=None):
        if uid is None: return int(self.index.get("uid/{}".format(inbox), 1))
        else: self.index["uid/{}".format(inbox)] = str(uid)

    # check if a message is already in the archive
    def __contains__(self, message):
        if not isinstance(message, Message):
            raise TypeError("message must be a Message object")
        return message.digest() in self.index

    # return a maildir instance for an inbox folder
    @functools.lru_cache(maxsize=8)
    def inbox(self, folder):
        folder = folder.replace("/", ".")
        # TODO: add optional urlcomponent quoting
        return Maildir(pjoin([self.path, folder]), create=True)

    # archive a message in mailbox
    def store(self, folder, message):
        if not isinstance(message, Message):
            message = Message(message)
        if message in self:
            raise FileExistsError("message already in index")
        inbox = self.inbox(folder)
        file = inbox.add(message)
        self.index[message.digest()] = b""
        self.log.warning("stored {}".format(file))
        return message.digest()



# Account is a configuration helper, parsing a section from a configuration file
# and for use in a with..as.. statement.
class Account:
    # parse account data from configuration section
    def __init__(self, section, logger=None):
        self.log = logger
        self._archive = pjoin([section.get("archive")])
        self.incremental = section.getboolean("incremental", True)
        self.exclude = section.get("exclude", "").strip().split("\n")
        self._server = section.get("server")
        self._username = section.get("username")
        self._password = section.get("password")

    def __enter__(self):

        self.server = Mailserver(self._server, self._username, self._password, logger=self.log)
        self.archive = Archive(self._archive, logger=self.log)

        return self, self.server, self.archive

    def __exit__(self, type, value, traceback):
        self.archive.close()
        self.server.connection.close()
        self.server.connection.logout()


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
    level = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level = level[min(len(level) - 1, args.verbose)]
    logging.basicConfig(format="%(name)s: %(message)s", level=level)
    log.debug(args)

    # read configuration
    log.info("read configuration from {}".format(args.config.name))
    conf = configparser.ConfigParser()
    conf.read_file(args.config)

    # iterate over configuration sections
    for section in conf.sections():

        # skip if sections given and it is not contained
        if len(args.section) >= 1 and section not in args.section:
            log.debug("section {} skipped".format(section))
            continue
        log.warning("processing section {}".format(section))
        sectlog = l(section)

        # if --list is given only connect and show folders
        if args.list:
            acc = Account(conf[section])
            serv = Mailserver(acc._server, acc._username, acc._password, logger=l(section))
            sectlog.info("listing folders:")
            for f in serv.ls():
                sectlog.warning(f)
            continue

        # open account for this section
        with Account(conf[section], logger=l(section)) as (acc, server, archive):

            # iterate over all folders
            for folder in server.ls():

                # test for exclusion matches
                try:
                    f = unquote(folder)
                    for ex in acc.exclude:
                        if fnmatch.fnmatch(f, ex):
                            sectlog.info("folder {} excluded due to '{}'".format(f, ex))
                            raise ValueError()
                except ValueError:
                    continue

                sectlog.warning("processing folder {}".format(folder))
                server.cd(folder)
                archive.move_old(folder)

                # retrieve the highest known uid for incremental runs
                uidkey = section + "/" + folder
                if not args.full and acc.incremental:
                    highest = int(archive.getuid(uidkey))
                    sectlog.debug("highest saved uid for {} = {}".format(uidkey, highest))
                else:
                    highest = 1
                    sectlog.info("starting at uid 1")

                # iterate over all uids >= highest
                for uid in server.mails(highest):

                    # email chunk generator
                    sectlog.debug("read email uid {}".format(int(uid)))
                    partials = server.partials(uid)
                    message = b""

                    # get enough chunks for header
                    while not (b"\r\n\r\n" in message or b"\n\n" in message):
                        message += next(partials)

                    # does the mail already exists?
                    if message in archive:
                        sectlog.info("message {} exists already".format(int(uid)))

                    else:

                        # assemble full message
                        for part in partials:
                            message += part
                        # save in archive
                        archive.store(message, unquote(folder))

                    # store highest seen uid per folder
                    if int(uid) > highest:
                        archive.setuid(uidkey, uid)
                        highest = int(uid)


if __name__ == "__main__":
    imapfetch()
