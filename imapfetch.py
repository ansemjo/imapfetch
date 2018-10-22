#!/usr/bin/env python3

import imaplib
import mailbox
import re
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("imapfetch")

# open imap connection
def connect(server, username, password):
    conn = imaplib.IMAP4_SSL(server)
    conn.login(username, password)
    return conn


# get new mails in current folder
def newmails(connection: imaplib.IMAP4_SSL, last_uid=1):
    # https://blog.yadutaf.fr/2013/04/12/fetching-all-messages-since-last-check-with-python-imap/

    # search for new message uids
    res, data = connection.uid("search", None, f"UID {last_uid}:*")
    if res != "OK":
        raise ValueError("failure while fetching new mail uids")

    # iterate over all message uids
    messages = data[0].split()
    for msg in messages:

        # search always returns at least one result
        if int(msg) > last_uid:

            # get message body
            res, data = connection.uid("fetch", msg, "(RFC822)")
            if res != "OK":
                raise ValueError(f"failure while fetching message {msg}")

            log.info(f"fetched mail uid {msg}")
            yield data[0][1]


def lsinbox(connection: imaplib.IMAP4_SSL):
    # get list of inboxes
    res, data = connection.list()
    if res != "OK":
        raise ValueError("failure getting folder list")
    sub = lambda s: re.sub(r"^\([^)]+\)\s\".\"\s", "", s)
    return (sub(f.decode()) for f in data)


# open maildir mailbox
def maildir(path: str):
    return mailbox.Maildir(path, create=True)


# save to maildir
def savemail(mailbox: mailbox.Mailbox, rfcbody: bytes):
    key = mailbox.add(rfcbody)
    m = mailbox.get_message(key)
    m.set_subdir("cur")
    m.add_flag("S")
    mailbox[key] = m
    log.info(f"saved mail {key}")


# -----------------------------------------------------------------------------------
if __name__ == "__main__":

    import configparser

    conf = configparser.ConfigParser()
    conf.read("settings.conf")
    settings = conf["imapfetch"]

    for section in [s for s in conf.sections() if s != "imapfetch"]:
        acc = conf[section]

        mb = maildir(acc.get("archive"))

        conn = connect(acc.get("server"), acc.get("username"), acc.get("password"))

        for inbox in lsinbox(conn):
            log.info(f"process inbox {inbox}")
            conn.select(inbox, readonly=True)
            folder = mb.add_folder(inbox)
            for mail in newmails(conn):
                savemail(folder, mail)
