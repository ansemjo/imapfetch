#!/usr/bin/env python3

import imaplib
import mailbox
import logging
import hashlib
import os
import re

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("imapfetch")

class Mailserver:

    # open connection
    def __init__(self, server, username, password):
        self.connection = imaplib.IMAP4_SSL(server)
        self.connection.login(username, password)

    # list available folders
    def ls(self):
        folders = self.connection.list()[1]
        return (re.sub(r"^\([^)]+\)\s\".\"\s", "", f.decode()) for f in folders)

    # "change directory"
    def cd(self, folder):
        self.connection.select(folder, readonly=True)

    # get new mails, starting with uidstart
    # https://blog.yadutaf.fr/2013/04/12/fetching-all-messages-since-last-check-with-python-imap/
    def newmails(self, uidstart=1):
        # search for and iterate over message uids
        uids = self.connection.uid("search", None, f"UID {uidstart}:*")[1]
        for msg in uids[0].split():
            # search always returns at least one result
            if int(msg) > uidstart:
                # fetch message body
                log.info(f"fetch mail uid {msg}")
                yield ImapMail(self, msg)

    # fetch email header
    def header(self, uid):
        data = self.connection.uid("fetch", uid, "(RFC822.HEADER)")[1]
        return data[0][1]

    def fullbody(self, uid):
        data = self.connection.uid("fetch", uid, "(RFC822)")[1]
        return data[0][1]


class ImapMail:
    def __init__(self, mailserv: Mailserver, uid):
        self._server = mailserv
        self.uid = uid
        self.header = self._server.header(self.uid)
        self.digest = digest(self.header)
        self._full = None

    def full(self):
        if self._full is None:
            self._full = self._server.fullbody(self.uid)
        return self._full


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
        server = Mailserver(acc.server, acc.username, acc.password)

        db = joinpath(acc.archive, "index")
        log.debug(f"open index at {db}")
        db = dbm.open(db, flag="c")

        for folder in server.ls():
            log.info(f"process folder {folder}")
            server.cd(folder)
            for mail in server.newmails():
                if mail.digest not in db:
                    key = mbox.store(mail.full(), folder)
                    db[mail.digest] = folder + "/" + key
                else:
                    print(f"message exists already")

        db.close()
        server.connection.close()
