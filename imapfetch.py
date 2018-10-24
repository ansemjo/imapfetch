#!/usr/bin/env python3

import imaplib
import mailbox
import logging
import hashlib
import configparser
import dbm
import email
import os
import re

# TODO: seperate logging per class/folder
logging.basicConfig(level=logging.DEBUG)
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
    def mails(self, uidstart=0):
        # search for and iterate over message uids
        uids = self.connection.uid("search", None, f"UID {uidstart}:*")[1]
        for uid in uids[0].split():
            if int(uid) > uidstart:
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
        self.log.debug(f"open archive in {path}")
        if not os.path.isdir(path):
            raise ValueError(f"path {path} is not a directory")
        self.dir = join(path)
        self.index = dbm.open(join("index", self.dir), flag="c")

    # cleanup
    def close(self):
        return self.index.close()

    # save a message in mailbox
    def store(self, body, folder):
        box = mailbox.Maildir(join(folder, self.dir), create=True)
        key = box.add(body)
        msg = box.get_message(key)
        msg.add_flag("S")
        msg.set_subdir("cur")
        box[key] = msg
        log.info(f"saved mail {key}")
        return key


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

    conf = configparser.ConfigParser()
    conf.read("settings.conf")

    for section in conf.sections():
        with Account(conf[section]) as (acc, server, archive):

            for folder in server.ls():
                log.info(f"process folder {folder}")

                server.cd(folder)
                highest = int(archive.index.get(folder, 0))

                for uid in server.mails(highest if acc.incremental else 1):
                    partials = server.partials(uid)

                    # get header chunk
                    message = next(partials)
                    while not (b"\r\n\r\n" in message or b"\n\n" in message):
                        message += next(partials)

                    # get digest of header only
                    e = email.message_from_bytes(message)
                    e.set_payload("")
                    digest = Blake2b(e.as_bytes())

                    # mail already exists?
                    if digest in archive.index:
                        log.debug("message exists already")

                    else:
                        # assemble full message
                        for part in partials:
                            message += part
                        # save in local mailbox
                        key = archive.store(message, re.sub(r'"(.*)"', r"\1", folder))
                        archive.index[digest] = folder + "/" + key
                        # save highest uid
                        if int(uid) > highest:
                            archive.index[folder] = uid
