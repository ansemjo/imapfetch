# imapfetch

`imapfetch.py` is a relatively straighforward Python script to download all emails from an IMAP4 mailserver and store them locally in a simple, plaintext maildir format, e.g. for backup purposes.

### New Version v1.0.0

The new development branch was finally merged into `main`. Unfortunately, it has a few incompatible changes, so I bumped the major version. **You cannot use v1 with old archives created with v0**, the new version will not know about the previously archived mails.

That being said, I think the changes are well worth the switch:

* uses `IMAPClient` library for safer communication to the server
* SHA224 header digests used in filename, as a sort of content-addressing scheme
* completely rewritten logging, silent by default
* switched index to a simple SQLite file

## INSTALL

You can quickly install directly from GitHub with `pip`:

    pip install git+https://github.com/ansemjo/imapfetch.git

For other options, see [packaging](#packaging) below.

## USAGE

Configure your accounts using the provided [configuration sample](assets/settings.cfg) and run:

    imapfetch settings.cfg

Use `--help` to see a list of possible options:

    imapfetch [-h] [--full] [--list] [--verbose] [--start-date START_DATE] [--end-date END_DATE] config [section ...]

The configuration file is passed as the first and only required positional argument. Any further positional arguments are section names from the configuration file, which will be run exclusively; for example if you want to archive only a single account at a time.

+ `--list`: Only show a list of folders for every account and exit. Useful to get an overview of your accounts before writing exclusion patterns or just checking connectivity.

* `--full`: Perform a full backup by starting with UID 1 in every folder; only useful if the server returns inconsistent or not strictly monotonically increasing UIDs. Duplicate mails which are already in the index will not be downloaded either way.

- `--verbose`: Show more verbose logging. Can be passed multiple times.

+ `--start-date START_DATE`: Start date for filtering messages (YYYY-MM-DD)

* `--end-date END_DATE`: End date for filtering messages (YYYY-MM-DD)

## CONFIGURATION

The available configuration options are mostly explained in the provided sample.

- you add one `[section]` per account
- `archive` points to the directory where you want to store the mails
- `server`, `username` and `password` are the IMAP4 connection details

- `exclude` is a multi-line string of UNIX-style globbing patterns to exclude folders from the
  backup; one pattern per line
- `quoting` enables urlencoding of folder names before writing to disk; some systems will not handle all allowed inbox characters otherwise

Minimal required sample:

    [myarchive]
    archive     = ~/mailarchive
    server      = imap.strato.de
    username    = max@mustermann.de
    password    = verySecurePassword

### RUNNING

During execution the `archive` directory is created if it does not exist and a simple `index.db` is created, which is an SQLite file.

For every backed up folder a subdirectory is created with the same name. Those subdirectories are [maildir](http://www.qmail.org/man/man5/maildir.html) mailboxes and can be viewed with most email clients; for example `mutt`.

For every E-Mail in that folder, the header is downloaded and hashed. If the resulting digest is not present in the index, the rest of the email is downloaded and stored in the local maildir. This is done to detect duplicates and avoid storing a mail twice if it is moved between folders.

    $ tree archive/ -L 2
    archive/
    ├── INBOX
    │   ├── cur
    │   ├── new
    │   └── tmp
    ├── index.db
    ├── muttrc
    ...

### BACKUP

Once `imapfetch` is done you have a local copy of all your emails. This is a **one-way operation**, no emails that are deleted online are ever deleted in your archive. You can then backup that entire directory as-is with tools like [borg](https://www.borgbackup.org/) or [restic](https://restic.net/). Both do a fantastic job at deduplicating existing data, so you don't waste much space even if you take daily or even hourly snapshots.

If you are sufficiently sure that you will never have an inbox folder called `backup` on your mailserver you might do something like this:

    borg init --encryption repokey-blake2 ./backup
    borg create --stats --progress --compression zstd \
      ./backup::$(date --utc +%F-%H%M%S%Z) INBOX*/ index

### VIEWING

Generally all applications that handle maildir mailboxes should be able to browse your archive. `mutt` is a nice terminal application that is able to handle these archives with an absolute minimum configuration. A sample is [provided](assets/muttrc) with this project: just copy the `muttrc` to your `archive` directory and run:

    cd path/to/my/archive
    mutt -F ./muttrc

Use <kbd>c</kbd> to change directories.

Generally, since maildir is a plaintext format, most commandline tools should work. `grep` is decently fast at finding specific emails, for example.

## PACKAGING

Make sure you run a decently modern version of Python 3; anything newer than `3.5` _should_ work but development was done on `3.10`.

### PIP

Since this project only uses a PEP 517 style `pyproject.toml`, you might have to update your `pip`. Install the package directly from GitHub as shown above or use a specific version archive:

    pip install [--user] https://github.com/ansemjo/imapfetch/archive/v1.0.0.tar.gz

### AUR

On Arch Linux install `imapfetch-git` with an AUR helper:

    paru -S imapfetch-git

### RPM, DEB, APK

Other packages can be built with [ansemjo/fpm](https://hub.docker.com/r/ansemjo/fpm/) using `assets/Makefile`:

    make -f assets/Makefile packages

These can then be installed locally with `yum` / `dnf` / `dpkg` etc.

### CONTAINER

Automatic GitHub workflows regularly [build a container image](https://github.com/ansemjo/imapfetch/pkgs/container/imapfetch) using `assets/Dockerfile`, which includes `crond` to run the script on a specific schedule easily. Run the container with `docker` or `podman` like this:

    docker run -d \
      -v ~/.config/imapfetch.cfg:/imapfetch.cfg \
      -v ~/mailarchive:/archive \
      -e SCHEDULE="*/15 * * * *" \
      ghcr.io/ansemjo/imapfetch

Make sure the `archive` key in your configuration points to the directory *as mounted inside the container*.

## LICENSE

The script is licensed under the [MIT License](LICENSE).
