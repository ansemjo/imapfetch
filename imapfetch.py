#!/usr/bin/env python3

import imaplib
import mailbox
import logging
import hashlib
import configparser
from argparse import Namespace
import dbm
import email
import os
import re

# TODO: seperate logging per class/folder
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("imapfetch")

# cryptographic hash for indexing purposes
Blake2b = lambda b: hashlib.blake2b(b, digest_size=32).digest()

# join path to absolute and expand ~ home
join = lambda p, base=".": os.path.abspath(os.path.join(base, os.path.expanduser(p)))

# Mailserver is a connection helper to an IMAP4 server.
#! Since most IMAP operations are performed on the currently selected folder
#! this is absolutely not safe for concurrent use. Weird things will happen.
class Mailserver:

    # open connection to server, pass imaplib.IMAP4 if you don't want TLS
    def __init__(self, server, username, password, log=log, IMAP=imaplib.IMAP4_SSL):
        self.log = log
        self.log.info(f"connect to {server} as {username}")
        self.connection = IMAP(server)
        self.connection.login(username, password)

    # list available folders
    def ls(self):
        folders = self.connection.list()[1]
        return (re.sub(r"^\([^)]+\)\s\".\"\s", "", f.decode()) for f in folders)

    # "change directory", readonly
    def cd(self, folder):
        self.connection.select(folder, readonly=True)

    # get new mail uids in current folder, starting with uidstart
    # https://blog.yadutaf.fr/2013/04/12/fetching-all-messages-since-last-check-with-python-imap/
    def mails(self, uidstart=1):
        # search for and iterate over message uids
        uids = self.connection.uid("search", None, f"UID {uidstart}:*")[1]
        for uid in uids[0].split():
            if int(uid) >= uidstart:
                yield uid

    # chunk sizes for partial fetches
    # 64 kB should be enough to fetch all possible headers and small messages in one go
    FIRSTCHUNK = 64 * 1024
    # for any larger messages use 1 MB chunks
    NEXTCHUNK = 1024 * 1024

    # partial generator for a single mail, for use with next() or for..in.. statements
    def partials(self, uid, firstchunk=FIRSTCHUNK, nextchunk=NEXTCHUNK):
        offset = 0
        chunksize = firstchunk
        while True:
            # partial fetch using BODY[]<o.c> syntax
            self.log.debug(f"partial fetch {int(uid)}: offset={offset} size={chunksize}")
            wrap, data = self.connection.uid("fetch", uid, f"BODY[]<{offset}.{chunksize}>")[1][0]
            yield data
            # check if the chunksize was smaller than requested --> assume no more data
            if int(re.sub(r".*BODY\[\].* {(\d+)}$", r"\1", wrap.decode())) < chunksize:
                return
            chunksize = nextchunk
            offset += chunksize


# Maildir is a maildir-based email storage for local on-disk archival.
# TODO: use plain named subfolders without dot-prefix (similar to thunderbird)
# TODO: integrate the index dbm directly
class Maildir:

    # open a new maildir mailbox
    def __init__(self, path, log=log):
        self.log = log
        self.dir = path = join(path)
        self.log.debug(f"open archive in {path}")
        if not os.path.isdir(path):
            raise ValueError(f"path {path} is not a directory")
        self.log.debug("open index")
        self.index = dbm.open(join("index", path), flag="c")

    # cleanup
    def close(self):
        return self.index.close()

    # get indexing key by hashing message header
    def digest(self, message):
        message = email.message_from_bytes(message)
        message.set_payload("")
        return Blake2b(message.as_bytes())

    # test if a message is already in the archive by hashing the header
    def __contains__(self, message):
        return self.digest(message) in self.index

    # save a message in mailbox
    def store(self, message, folder):
        box = mailbox.Maildir(join(folder, self.dir), create=True)
        key = box.add(message)
        msg = box.get_message(key)
        msg.add_flag("S")
        msg.set_subdir("cur")
        box[key] = msg
        self.index[self.digest(message)] = folder + "/" + key
        log.info(f"saved mail {key}")
        return key

    # store uid per folder
    def setuid(self, folder, uid):
        self.index["UID:" + folder] = uid

    # retrieve highest seen uid per folder, default 0
    def getuid(self, folder):
        return self.index.get("UID:" + folder, 1)


# Account is a configuration helper, parsing a section from a configuration file
# and for use in a with..as.. statement.
class Account:
    # parse account data from configuration section
    def __init__(self, section):
        self._archive = join(section.get("archive", "./archive"))
        self.incremental = section.getboolean("incremental", True)
        self._server = section.get("server")
        self._username = section.get("username")
        self._password = section.get("password")

    def __enter__(self):

        self.server = Mailserver(self._server, self._username, self._password)
        self.archive = Maildir(self._archive)

        return self, self.server, self.archive

    def __exit__(self, type, value, traceback):
        self.archive.close()
        self.server.connection.close()
        self.server.connection.logout()


# -----------------------------------------------------------------------------------
if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="configuration file", type=argparse.FileType("r"))
    parser.add_argument("section", help="sections to execute", nargs="*")
    parser.add_argument("--full", help="do full backups", action="store_true")
    args = parser.parse_args()

    conf = configparser.ConfigParser()
    conf.read_file(args.config)

    for section in conf.sections():

        if len(args.section) >= 1 and section not in args.section:
            continue

        with Account(conf[section]) as (acc, server, archive):

            for folder in server.ls():

                log.info(f"process folder {folder}")
                server.cd(folder)
                uidkey = section + "/" + folder
                highest = int(archive.getuid(uidkey)) if not args.full and acc.incremental else 1

                for uid in server.mails(highest):

                    # email chunk generator
                    partials = server.partials(uid)
                    message = b""

                    # get enough chunks for header
                    while not (b"\r\n\r\n" in message or b"\n\n" in message):
                        message += next(partials)

                    # does the mail already exists?
                    if message in archive:
                        log.debug("message exists already")

                    else:

                        # assemble full message
                        for part in partials:
                            message += part
                        # save in archive
                        archive.store(message, re.sub(r'"(.*)"', r"\1", folder))

                    # store highest seen uid per folder
                    if int(uid) > highest:
                        archive.setuid(uidkey, uid)
                        highest = int(uid)

