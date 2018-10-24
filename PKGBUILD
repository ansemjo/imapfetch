# Maintainer: Anton Semjonov <anton@semjonov.de>

pkgname="imapfetch-git"
_pkgname=(${pkgname%-git})
pkgdesc="Download all e-mails from an IMAP4 mailserver and store them locally in maildir format."

pkgver=0.1.0.r0.8284474
pkgrel=1

arch=('any')
url="https://github.com/ansemjo/$_pkgname"
license=('MIT')

depends=('python')
makedepends=('git')

provides=($_pkgname)
conflicts=($_pkgname)

source=("$pkgname::git+https://github.com/ansemjo/imapfetch.git")
sha256sums=('SKIP')

pkgver() {
  cd "$srcdir"
  printf "%s" "$(git describe --long | sed 's/\([^-]*-\)g/r\1/;s/-/./g')"

}

package() {
  cd "$pkgname"
  install -D -m 755 "${_pkgname}/${_pkgname}.py"  -T "${pkgdir}/usr/bin/${_pkgname}"
  install -D -m 644 "assets/muttrc"               -t "${pkgdir}/usr/share/doc/${_pkgname}/"
  install -D -m 644 "assets/settings.conf.sample" -t "${pkgdir}/usr/share/doc/${_pkgname}/"
  install -D -m 644 "README.md"                   -t "${pkgdir}/usr/share/doc/${_pkgname}/"
}
