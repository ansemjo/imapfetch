# get the version from the file
VERSION = $(shell python -c "$$(sed '/^import/,$$ d' imapfetch.py); print(__version__)")

# build sdist with flit
dist/imapfetch-$(VERSION).tar.gz: imapfetch.py pyproject.toml
	flit build --setup-py

# extract sdist in packaging directory
pkg/imapfetch-$(VERSION)/: dist/imapfetch-$(VERSION).tar.gz
	@mkdir -p pkg/
	tar -xf $< -C pkg/
	# patch the setup.py to use setuptools
	#sed -i 's/^from distutils.core/from setuptools/' $@/setup.py

# create packages from source dist with fpm
PKGFORMATS = rpm deb apk
FPM = docker run --rm -v $$PWD/pkg:/pkg -w /pkg ghcr.io/ansemjo/fpm
FPMARGS = \
	--python-install-bin /usr/bin \
	--python-install-lib /usr/lib/python3/site-packages \
	--python-bin /usr/bin/python3

# build a package
.PHONY: package package/%
package : $(addprefix package/,$(PKGFORMATS))
package/% : pkg/imapfetch-$(VERSION)/
	$(FPM) $(FPMARGS) -s python -t $* -f $(<:pkg/%=%)

# build packages for depended packages too
.PHONY: depends depends/%
depends : $(addprefix depends/,$(PKGFORMATS))
depends/% :
	$(FPM) $(FPMARGS) -s python -t $* -f IMAPClient
	$(FPM) $(FPMARGS) -s python -t $* -f six
