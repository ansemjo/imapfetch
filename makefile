.DEFAULT_GOAL := docker-fpm

# packaging directory
DESTDIR = pkg
BINDIR  = $(DESTDIR)/usr/bin
DOCDIR  = $(DESTDIR)/usr/share/doc/imapfetch

# command to install file
INSTALL = install -D -T $< $@

# install files for packaging
.PHONY: install
install : \
	$(BINDIR)/imapfetch \
	$(DOCDIR)/muttrc \
	$(DOCDIR)/imapfetch.conf.sample \
	$(DOCDIR)/README.md

$(BINDIR)/imapfetch : imapfetch/imapfetch.py ;	$(INSTALL) -m 755
$(DOCDIR)/muttrc : assets/muttrc ;	$(INSTALL) -m 644
$(DOCDIR)/imapfetch.conf.sample : assets/settings.conf.sample ;	$(INSTALL) -m 644
$(DOCDIR)/README.md : README.md ;	$(INSTALL) -m 644

# package metadata
PKGNAME     := imapfetch
PKGVERSION  := $(shell sh version.sh describe | sed s/-/./ )
PKGAUTHOR   := 'ansemjo <anton@semjonov.de'
PKGLICENSE  := MIT
PKGURL      := https://github.com/ansemjo/$(PKGNAME)

# packaging formats
PKGFORMATS = rpm deb apk

# how to execute fpm
FPM = podman run --rm --net none -v $$PWD:/build -w /build ansemjo/fpm:alpine

# build a package
.PHONY: package-%
package-% : install
	$(FPM) -s dir -t $* -f --chdir $(DESTDIR) \
		--name $(PKGNAME) --version $(PKGVERSION) \
		--maintainer $(PKGAUTHOR) --license $(PKGLICENSE) --url $(PKGURL)

# build all package formats with fpm
packages : $(addprefix package-,$(PKGFORMATS))
