SHELL := /usr/bin/bash
.DEFAULT_GOAL := docker-fpm

# packaging directory
DESTDIR = pkg

# command to install file
INSTALL = install -D -T $< $@

.PHONY: install
install : \
	$(DESTDIR)/usr/bin/imapfetch \
	$(DESTDIR)/usr/share/doc/imapfetch/muttrc \
	$(DESTDIR)/usr/share/doc/imapfetch/imapfetch.conf.sample \
	$(DESTDIR)/usr/share/doc/imapfetch/README.md

$(DESTDIR)/usr/bin/imapfetch : imapfetch/imapfetch.py
	$(INSTALL) -m 755
$(DESTDIR)/usr/share/doc/imapfetch/muttrc : assets/muttrc
	$(INSTALL) -m 644
$(DESTDIR)/usr/share/doc/imapfetch/imapfetch.conf.sample : assets/settings.conf.sample
	$(INSTALL) -m 644
$(DESTDIR)/usr/share/doc/imapfetch/README.md : README.md
	$(INSTALL) -m 644

# packaging formats
FORMATS = rpm deb apk

# package version
VERSION := 0.2.0-$Format:%h$
VERSION := $(shell [[ $(VERSION) = *-ormat:* ]] && git describe --always || echo $(VERSION))

.PHONY: fpm
fpm : $(FORMATS)

.PHONY: $(FORMATS)
$(FORMATS) : install
	fpm -s dir -t $@ -f \
		--name imapfetch \
		--version $(VERSION) \
		--maintainer 'ansemjo <anton@semjonov.de>' \
		--vendor fpm-builder \
		--license MIT \
		--url https://github.com/ansemjo/aenker \
		--chdir $(DESTDIR)

FPM_IMAGE := registry.rz.semjonov.de/docker/fpm-builder:latest

.PHONY: docker-fpm
docker-fpm :
	docker run --rm -v $$PWD:/build $(FPM_IMAGE) make fpm
