# imapfetch

`imapfetch.py` is a relatively straighforward Python script to download all emails from an IMAP4
server and store them locally in a maildir format, e.g. for backup purposes.

## SYNOPSIS

Configure your accounts using the provided configuration [sample](assets/settings.conf.sample) and
run:

    imapfetch settings.conf

## USAGE

Use `--help` to see a list of possible options:

    imapfetch [-h] [--full] [--list] [--verbose] config [section [section ...]]

The configuration file is passed as the first positional argument. If you pass any more positional
arguments, they are assumed to be section names and only those sections will be used during
execution.

The flags include:

- `--full`: always perform full backup by starting with UID 1; useful if the server returns
  inconsistent or not monotonically increasing UIDs; duplicate mails will not be saved regardless
- `--list`: only show a list of folders for every account and exit; useful to get an overview before
  writing exclusion patterns
- `--verbose`: show more verbose logging, can be passed up to two times

## CONFIGURATION

The available configuration options are mostly explained in the provided sample.

- you add one `[section]` per account
- `archive` points to the directory where you want to store the mails
- `server`, `username` and `password` are the IMAP4 connection details

Optional settings include:

- `incremental` takes a boolean value whether to perform incremental backups by saving the highest
  seen UID
- `exclude` is a multi-line string of UNIX-style globbing patterns to exclude folders from the
  backup, one pattern per line

Minimal required sample:

    [myarchive]
    archive     = ./archive
    server      = imap.strato.de
    username    = max@mustermann.de
    password    = verySecurePassword

### RUNNING

During execution the `archive` directory is created if it does not exist and a simple `index` is
created, which is a [DBM](https://www.gnu.org/software/gdbm/) file.

For every backed up folder a subdirectory is created with the same name. Those subdirectories are
[maildir](http://www.qmail.org/man/man5/maildir.html) mailboxes and can be viewed e.g. with `mutt`.

For every E-Mail in that folder the header is downloaded and hashed with Blake2b. If the resulting
digest is not present in the `index` the entire mail is downloaded and stored in the local mailbox.
This is done to detect duplicates and avoid storing a mail twice if it is moved between folders.

    $ tree archive/ -L 2
    archive/
    ├── INBOX
    │   ├── cur
    │   ├── new
    │   └── tmp
    ├── index
    ├── muttrc
    ...

### BACKUP

Once `imapfetch` is done you have a local copy of all your mails. This is a one-way operation, no
mails that are deleted online are deleted in your archive. Since the maildir format is a very simple
one, you can then backup that entire directory as-is with tools like
[borg](https://www.borgbackup.org/) or [restic](https://restic.net/). Both do a fantastic job at
deduplicating existing data, so you don't waste much space even if you take daily or even hourly
snapshots.

If you are sufficiently sure that you will never have a folder called `backup` on your mailserver
you might do something like this:

    borg init --encryption repokey-blake2 ./backup
    borg create --stats --progress --compression lz4 ./backup::$(date --utc +%F-%H%M%S%Z) INBOX*/ index

### VIEWING

Generally all applications that handle maildir mailboxes should be able to browse your archive.
`mutt` is a very simple terminal application that is able to handle these archives with an absolute
minimum configuration. A sample is [provided](assets/muttrc) with this project. Just copy the
`muttrc` to your `archive` directory and run:

    cd path/to/my/archive
    mutt -F ./muttrc

Use <kbd>c</kbd> to change directories.

Generally, since `maildir` is a plaintext format, most commandline tools should work. `grep` is
decently fast for example.

## INSTALL

Make sure you run a decently modern Python 3 (anything newer than `3.5` _should_ work, development
was done on `3.7`).

**Note:** When running on Python 3.5 (e.g. on Debian 9 "stretch") you need to install
[pyblake2](https://pypi.org/project/pyblake2/) because the `hashlib` does not include `blake2b` in
3.5 yet.

### PIP

Install the package via `pip` by running:

    pip install https://github.com/ansemjo/imapfetch/archive/master.tar.gz [--user]

### AUR

On Arch Linux install `imapfetch` with an AUR helper:

    yay -S imapfetch

Or use the `PKGBUILD` in `master` to build `imapfetch-git` locally:

    makepkg -i

### RPM, DEB, APK

Other packages can be built with [ansemjo/fpm](https://hub.docker.com/r/ansemjo/fpm/) using the
`makefile` or downloaded from the [releases page](https://github.com/ansemjo/imapfetch/releases).
They can then be installed locally with `yum`/`dnf`/`dpkg` etc.

    make packages

## CONTAINER

The script can also be scheduled to run regularly with cron inside a container with
[ansemjo/imapfetch](https://hub.docker.com/r/ansemjo/imapfetch/). Download or build the image with
the provided `Dockerfile`, then run the container with `docker` or `podman`:

    podman run -d \
      -v ~/.config/imapfetch.cfg:/config \
      -v ~/Documents/Email-Archive:/archive \
      ansemjo/imapfetch

Mount your configuration file and the root directory of your configured archives in the container as
seen above.

## LICENSE

The script is licensed under the [MIT License](LICENSE).
