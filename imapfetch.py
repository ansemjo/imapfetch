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

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("imapfetch")


class Mailserver:

    # open connection
    def __init__(self, server, username, password, log=log):
        self.log = log
        self.log.info(f"connect to {server} as {username}")
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
        for uid in uids[0].split():
            # search always returns at least one result
            if int(uid) > uidstart:
                # fetch message body
                yield uid

    FIRSTCHUNK = 64 * 1024
    NEXTCHUNK = 1024 * 1024

    # partial generator for a single mail
    def partials(self, uid):
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
    def __init__(self, path, log=log):
        self.log = log
        self.log.debug(f"open archive in {path}")
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
        self._archive = joinpath(".", section.get("archive"))
        self._index = joinpath(self._archive, "index")
        self.incremental = section.getboolean("incremental")
        self._server = section.get("server")
        self._username = section.get("username")
        self._password = section.get("password")

    def __enter__(self):

        self.server = Mailserver(self._server, self._username, self._password)
        self.archive = Maildir(self._archive)
        log.debug(f"open index in {self._index}")
        self.index = dbm.open(self._index, flag="c")

        return self, self.server, self.archive, self.index

    def __exit__(self, type, value, traceback):
        self.index.close()
        self.server.connection.close()
        self.server.connection.logout()


# hash message header for indexing
def digest(header):
    return hashlib.blake2b(header).digest()


# join paths and return absolute
def joinpath(base, path):
    return os.path.abspath(os.path.join(base, os.path.expanduser(path)))


# -----------------------------------------------------------------------------------
if __name__ == "__main__":

    conf = configparser.ConfigParser()
    conf.read("settings.conf")

    for section in conf.sections():
        with Account(conf[section]) as (acc, server, archive, index):

            for folder in server.ls():
                log.info(f"process folder {folder}")

                server.cd(folder)
                highest = int(index.get(folder, 1))

                for uid in server.mails(highest if acc.incremental else 1):
                    partials = server.partials(uid)

                    # get header chunk
                    message = next(partials)
                    while not (b"\r\n\r\n" in message or b"\n\n" in message):
                        message += next(partials)

                    # get digest of header only
                    e = email.message_from_bytes(message)
                    e.set_payload("")
                    dgst = digest(e.as_bytes())

                    # mail already exists?
                    if dgst in index:
                        log.debug("message exists already")

                    else:
                        # assemble full message
                        for part in partials:
                            message += part
                        # save in local mailbox
                        key = archive.store(message)
                        index[dgst] = folder + "/" + key
                        # save highest uid
                        if int(uid) > highest:
                            index[folder] = uid
