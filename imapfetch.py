#!/usr/bin/env python3

import imaplib
import mailbox
import re
import logging

logging.basicConfig(level=logging.INFO)
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

    # select a folder readonly
    def select(self, folder=None):
        return self.connection.select(folder, readonly=True)

    # get new mails, starting with uidstart
    # https://blog.yadutaf.fr/2013/04/12/fetching-all-messages-since-last-check-with-python-imap/
    def mails(self, uidstart=1):
        # search for and iterate over message uids
        uids = assertok(
            self.connection.uid("search", None, f"UID {uidstart}:*"), "failure while fetching new mail uids"
        )
        for msg in uids[0].split():
            # search always returns at least one result
            if int(msg) > uidstart:
                # fetch message body
                log.info(f"fetch mail uid {msg}")
                data = assertok(
                    self.connection.uid("fetch", msg, "(RFC822)"), f"failure while fetching message {msg}"
                )
                yield data[0][1]


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


# -----------------------------------------------------------------------------------
if __name__ == "__main__":

    import configparser

    conf = configparser.ConfigParser()
    conf.read("settings.conf")
    settings = conf["imapfetch"]

    for section in [s for s in conf.sections() if s != "imapfetch"]:
        acc = conf[section]

        log.debug(f"open maildir in {acc.get('archive')}")
        mbox = Maildir(acc.get("archive"))

        log.debug(f"connect to {acc.get('server')}")
        server = IMAP4Server(acc.get("server"), acc.get("username"), acc.get("password"))

        for folder in server.ls():
            log.info(f"process folder {folder}")
            server.select(folder)
            for mail in server.mails():
                mbox.store(mail, folder)
