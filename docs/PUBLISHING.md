# Publishing the first release

1. Create or select `snuffy420/FileBurglar`, or update the repository links if using another name.
2. Upload the **contents** of `Repository` to the repository root. Do not upload your working program folder: it contains your private index and settings.
3. Enable GitHub Pages from `main`, root folder, for the contact page.
4. Create a release tagged **v1.4.5**, titled **File Burglar 1.4.5 — initial Windows release**.
5. Paste `RELEASE-NOTES.md` into the release description and attach the files in `Release-Assets`.
6. Keep the source ZIP/7z alongside the installer and portable downloads, with the included license/source-access information and checksums.
7. After publication, check the download links, Pages contact page, and Formspree configuration. Do not send a live test message unless you intend to contact the configured inbox.

The upload folder also contains an extracted portable application for inspection. Upload its archive to the release rather than adding compiled DLLs to the source repository. The GitHub Actions workflow builds artifacts; it does not automatically publish a release.

The installer preserves per-user application data on uninstall. No startup task is created until the user chooses Start with Windows in the app. The installer/uninstaller removes a logon task only if its executable points to that installation.
