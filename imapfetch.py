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

# colorful logging levels (with added VERBOSE intermediate)
INFO, VERBOSE, DEBUG = logging.INFO, 15, logging.DEBUG
logging.addLevelName(INFO, "\033[34;1mINFO\033[0m")
logging.addLevelName(VERBOSE, "\033[33;1mVERBOSE\033[0m")
logging.addLevelName(DEBUG, "\033[35;1mDEBUG\033[0m")

# shorthand to get a named logger
log = logging.getLogger("imapfetch")
l = lambda n: log.getChild(n)

# cryptographic hash for indexing purposes
Blake2b = lambda b: hashlib.blake2b(b, digest_size=32).digest()

# join path to absolute and expand ~ home
join = lambda p, base=".": os.path.abspath(os.path.join(base, os.path.expanduser(p)))

# remove quotes around folder name
unquote = lambda f: re.sub(r'"(.*)"', r"\1", f)

# Mailserver is a connection helper to an IMAP4 server.
#! Since most IMAP operations are performed on the currently selected folder
#! this is absolutely not safe for concurrent use. Weird things will happen.
class Mailserver:

    # open connection to server, pass imaplib.IMAP4 if you don't want TLS
    def __init__(self, server, username, password, logger=l("mailserver"), IMAP=imaplib.IMAP4_SSL):
        self.log = logger.log
        self.log(VERBOSE, f"connect to {server} as {username}")
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
            self.log(DEBUG, f"partial fetch {int(uid)}: offset={offset} size={chunksize}")
            wrap, data = self.connection.uid("fetch", uid, f"BODY[]<{offset}.{chunksize}>")[1][0]
            yield data
            # check if the chunksize was smaller than requested --> assume no more data
            if int(re.sub(r".*BODY\[\].* {(\d+)}$", r"\1", wrap.decode())) < chunksize:
                return
            chunksize = nextchunk
            offset += chunksize


# Maildir is a maildir-based email storage for local on-disk archival.
class Maildir:

    # open a new maildir mailbox
    def __init__(self, path, logger=l("archive")):
        self.log = logger.log
        self.dir = path = join(path)
        self.log(VERBOSE, f"open archive in {path}")
        os.makedirs(path, exist_ok=True)
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
        self.log(INFO, f"archived mail {key}")
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
        self._archive = join(section.get("archive"))
        self.incremental = section.getboolean("incremental", True)
        self.exclude = section.get("exclude", "").strip().split("\n")
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
    import fnmatch

    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="configuration file", type=argparse.FileType("r"))
    parser.add_argument("section", help="sections to execute", nargs="*")
    parser.add_argument("--full", "-f", help="do full backups", action="store_true")
    parser.add_argument("--list", "-l", help="only list folders", action="store_true")
    parser.add_argument("--verbose", "-v", help="increase verbosity", action="count", default=0)
    args = parser.parse_args()

    # initialise logging level and format
    clamp = lambda v, l, u: v if v > l else l if v < u else u
    logging.basicConfig(
        level=clamp(20 - 5 * args.verbose, 10, 20), format="[%(levelname)18s] %(name)s: %(message)s"
    )
    log.log(DEBUG, args)

    # read configuration
    log.log(VERBOSE, f"read configuration from {args.config.name}")
    conf = configparser.ConfigParser()
    conf.read_file(args.config)

    # iterate over configuration sections
    for section in conf.sections():

        # skip if sections given and it is not contained
        if len(args.section) >= 1 and section not in args.section:
            log.log(DEBUG, f"section {section} skipped")
            continue
        log.log(INFO, f"processing section {section}")
        sectlog = l(section).log

        # if --list is given only connect and show folders
        if args.list:
            acc = Account(conf[section])
            serv = Mailserver(acc._server, acc._username, acc._password)
            sectlog(VERBOSE, "listing folders:")
            for f in serv.ls():
                sectlog(INFO, f)
            continue

        # open account for this section
        with Account(conf[section]) as (acc, server, archive):

            # iterate over all folders
            for folder in server.ls():

                # test for exclusion matches
                try:
                    f = unquote(folder)
                    for ex in acc.exclude:
                        if fnmatch.fnmatch(f, ex):
                            sectlog(VERBOSE, f"folder {f} excluded due to '{ex}'")
                            raise ValueError()
                except ValueError:
                    continue

                sectlog(INFO, f"processing folder {folder}")
                server.cd(folder)

                # retrieve the highest known uid for incremental runs
                uidkey = section + "/" + folder
                if not args.full and acc.incremental:
                    highest = int(archive.getuid(uidkey))
                    sectlog(DEBUG, f"highest saved uid for {uidkey} = {highest}")
                else:
                    highest = 1
                    sectlog(VERBOSE, "starting at uid 1")

                # iterate over all uids >= highest
                for uid in server.mails(highest):

                    # email chunk generator
                    sectlog(DEBUG, f"read email uid {int(uid)}")
                    partials = server.partials(uid)
                    message = b""

                    # get enough chunks for header
                    while not (b"\r\n\r\n" in message or b"\n\n" in message):
                        message += next(partials)

                    # does the mail already exists?
                    if message in archive:
                        sectlog(VERBOSE, f"message {int(uid)} exists already")

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

