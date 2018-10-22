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
    FIRSTCHUNK = 64 * 1024
    NEXTCHUNK = 1024 * 1024

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
    def mails(self, uidstart=1):
        # search for and iterate over message uids
        uids = self.connection.uid("search", None, f"UID {uidstart}:*")[1]
        for msg in uids[0].split():
            # search always returns at least one result
            if int(msg) > uidstart:
                # fetch message body
                log.info(f"fetch mail uid {msg}")
                yield self.generator(msg)

    # partial generator for a single mail
    def generator(self, uid):
        offset = 1
        chunksize = self.FIRSTCHUNK
        while True:
            log.debug(f"UID PARTIAL {uid} RFC822 offset={offset} size={chunksize}")
            wrap, data = self.connection.uid("partial", uid, "RFC822", str(offset), str(chunksize))[1][0]
            yield data
            if int(re.sub(r".*RFC822 {(\d+)}$", r"\1", wrap.decode())) < chunksize:
                return
            chunksize = self.NEXTCHUNK
            offset += chunksize


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
    import email

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
            for mailgen in server.mails():

                # get header chunk
                message = next(mailgen)
                while not (b"\r\n\r\n" in message or b"\n\n" in message):
                    message += next(mailgen)

                # get digest of header only
                e = email.message_from_bytes(message)
                e.set_payload("")
                dgst = digest(e.as_bytes())

                # mail already exists?
                if dgst in db:
                    log.debug("message exists already")

                else:
                    # assemble full message
                    for part in mailgen:
                        message += part
                    # save in local mailbox
                    key = mbox.store(message, folder)
                    db[dgst] = folder + "/" + key

        db.close()
        server.connection.close()
