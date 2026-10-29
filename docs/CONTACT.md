# Contact page

The existing maintainer endpoint is `https://formspree.io/f/myeekrpy`. File Burglar uses the subject **File Burglar contact**. Automated packaging tests do not submit messages to the live endpoint.

Upload `docs/contact.html`, `assets/fileburglar.png`, `assets/contact-button.svg`, and the root `.nojekyll` file with the repository. For the assumed `snuffy420/FileBurglar` repository, enable GitHub Pages from **main → / (root)**. The README contact button opens:

https://snuffy420.github.io/FileBurglar/docs/contact.html

If the repository name changes, update the README contact URL, page footer, and installer support URL before publishing. Confirm that the existing Formspree account permits the GitHub Pages origin. Messages go through Formspree to its configured destination; the repository stores no submitted messages.

The same page is included with the portable and installed packages. It can open locally; sending still requires Internet access and an active Formspree endpoint. No message was sent as part of release verification.
