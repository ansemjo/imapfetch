#!/usr/bin/env python3

import imaplib
import mailbox
import logging
import hashlib
import os
import re

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("imapfetch")

# raise errors if returned status is not OK
def assertok(obj, message=None):
    if obj[0] != "OK":
        raise ValueError(message if message is not None else obj[0])
    return obj[1]


class IMAP4Server:

    # open connection
    def __init__(self, server, username, password):
        self.connection = imaplib.IMAP4_SSL(server)
        self.connection.login(username, password)

    # list available folders
    def ls(self):
        folders = assertok(self.connection.list(), "failure getting folder list")
        return (re.sub(r"^\([^)]+\)\s\".\"\s", "", f.decode()) for f in folders)

    # get new mails, starting with uidstart
    # https://blog.yadutaf.fr/2013/04/12/fetching-all-messages-since-last-check-with-python-imap/
    def mails(self, folder=None, uidstart=1):
        # select folder readonly
        self.connection.select(folder, readonly=True)
        # search for and iterate over message uids
        res = self.connection.uid("search", None, f"UID {uidstart}:*")
        uids = assertok(res, "failure while fetching new mail uids")
        for msg in uids[0].split():
            # search always returns at least one result
            if int(msg) > uidstart:
                # fetch message body
                log.info(f"fetch mail uid {msg}")
                res = self.connection.uid("fetch", msg, "(RFC822.HEADER RFC822.TEXT)")
                data = assertok(res, f"failure while fetching message {msg}")
                # yields [header, body]
                yield [p[1] for p in data[:2]]


class Maildir:
    # open a new maildir mailbox
    def __init__(self, path):
        self.box = mailbox.Maildir(path, create=True)

    # save a message in mailbox
    def store(self, body, folder=None):
        f = self.box if folder is None else self.box.add_folder(folder)
        key = f.add(body)
        msg = f.get_message(key)
        msg.add_flag("S")
        msg.set_subdir("cur")
        f[key] = msg
        log.info(f"saved mail {key}")
        return key


class Account:
    # parse account data from configuration section
    def __init__(self, section):
        self.archive = joinpath(".", section.get("archive"))
        self.server = section.get("server")
        self.username = section.get("username")
        self.password = section.get("password")


# hash message header for indexing
def digest(header):
    return hashlib.blake2b(header).digest()


# join paths and return absolute
def joinpath(base, path):
    return os.path.abspath(os.path.join(base, os.path.expanduser(path)))


# -----------------------------------------------------------------------------------
if __name__ == "__main__":

    import configparser
    import dbm

    conf = configparser.ConfigParser()
    conf.read("settings.conf")
    settings = conf["imapfetch"]

    for section in [s for s in conf.sections() if s != "imapfetch"]:
        acc = Account(conf[section])

        log.debug(f"open maildir in {acc.archive}")
        mbox = Maildir(acc.archive)

        log.debug(f"connect to {acc.server}")
        server = IMAP4Server(acc.server, acc.username, acc.password)

        db = joinpath(acc.archive, "index")
        log.debug(f"open index at {db}")
        db = dbm.open(db, flag="c")

        for folder in server.ls():
            log.info(f"process folder {folder}")
            for header, body in server.mails(folder):
                H = digest(header)
                if H not in db:
                    key = mbox.store(header + body, folder)
                    db[H] = folder + "/" + key
                else:
                    print(f"message {H} exists already")

        db.close()
        server.connection.close()
