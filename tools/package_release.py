"""Stage a clean repository and verified Windows release assets."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from appinfo import APP_VERSION

SOURCE_FILES = [
    'about_dialog.py','appinfo.py','diagnostics.py','engine.py','indexer.py','main.py','selftest.py',
    'settings.py','smart.py','startup.py','storage.py','ui.py','watcher.py','folder_updates.py',
    'test_fileburglar.py','test_explorer_gui.py','test_explorer_smart.py','test_release_support.py','test_monitoring.py','test_about.py',
    'FileBurglar.cmd','FileBurglar.spec','requirements.txt','requirements-build.txt',
    'README.md','CHANGELOG.md','LICENSE','THIRD_PARTY_NOTICES.md','.gitignore','.nojekyll']
ASSET_FILES = ['fileburglar.ico','fileburglar.png','contact-button.svg',
               'installer-side.bmp','installer-small.bmp','donation-btc-qr.png']
PRIVATE = {'settings.json','fileburglar.db','fileburglar.db-wal','fileburglar.db-shm'}


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def clean_files(folder):
    files=sorted(p for p in folder.rglob('*') if p.is_file())
    for path in files:
        assert path.name.lower() not in PRIVATE,path
        assert path.suffix.lower() not in ('.db','.log','.pyc','.pyo'),path
        assert not any(part in ('__pycache__','.freebuff','.review-backups') for part in path.parts),path
    return files


def copy_source(destination):
    destination.mkdir(parents=True)
    for name in SOURCE_FILES:
        shutil.copy2(ROOT/name,destination/name)
    for directory in ('docs','tools','licenses','installer','.github'):
        shutil.copytree(ROOT/directory,destination/directory,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo'))
    (destination/'assets/screenshots').mkdir(parents=True)
    for name in ASSET_FILES:
        shutil.copy2(ROOT/'assets'/name,destination/'assets'/name)
    for name in ('main.png','explorer.png','about.png','help.png','donate.png','system-tray.png','tray-menu.png','tray-drive-menu.png'):
        shutil.copy2(ROOT/'assets/screenshots'/name,destination/'assets/screenshots'/name)
    clean_files(destination)
    return destination


def sevenzip():
    path=shutil.which('7z')
    if path:return Path(path)
    path=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'7-Zip/7z.exe'
    if path.is_file():return path
    raise RuntimeError('Install 7-Zip on the build machine.')


def compiler():
    configured=os.environ.get('INNO_ISCC')
    if configured and Path(configured).is_file():return Path(configured)
    for base in (os.environ.get('ProgramFiles','C:/Program Files'),os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)')):
        for version in ('7','6'):
            path=Path(base)/f'Inno Setup {version}'/'ISCC.exe'
            if path.is_file():return path
    raise RuntimeError('Set INNO_ISCC to your Inno Setup ISCC.exe.')


def archive_7z(folder,output):
    tool=str(sevenzip())
    subprocess.run([tool,'a','-t7z','-mx=5','-mmt=2','-bd','-bso0','-bsp0',str(output),folder.name],
                   cwd=folder.parent,check=True)
    subprocess.run([tool,'t','-bd','-bso0','-bsp0',str(output)],check=True)


def smoke(executable,report):
    subprocess.run([str(executable),'--self-test',str(report)],timeout=45,check=True,
                   creationflags=0x08000000)
    data=json.loads(report.read_text(encoding='utf-8'))
    assert data['passed'] and data['frozen']
    return data


def package(payload,output):
    payload=payload.resolve()
    assert (payload/'FileBurglar.exe').is_file(),'Build FileBurglar.spec first.'
    release=(output/f'FileBurglar-{APP_VERSION}').resolve()
    if release.exists():raise FileExistsError(f'Refusing to overwrite {release}')
    tool=compiler();sevenzip()
    release.mkdir(parents=True)
    scratch_root=ROOT.parent/'.release-build'
    scratch_root.mkdir(exist_ok=True)
    scratch=scratch_root/('package-'+uuid.uuid4().hex);scratch.mkdir()
    try:
        repo=copy_source(release/'Repository')
        assets=release/'Release-Assets';assets.mkdir()
        portable=release/'Portable'/f'FileBurglar-{APP_VERSION}-windows-x64-portable'
        shutil.copytree(payload,portable)
        (portable/'portable.txt').write_text('File Burglar portable mode. Keep this marker beside FileBurglar.exe.\n',encoding='utf-8')
        (portable/'Data').mkdir()
        (portable/'Data/README.txt').write_text('Private index, settings and logs are created here when you run File Burglar.\nNo personal data is included in this package.\n',encoding='utf-8')
        for name in ('README.md','CHANGELOG.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(repo/name,portable/name)
        for directory in ('docs','licenses','assets'):
            shutil.copytree(repo/directory,portable/directory)
        clean_files(portable)
        result=smoke(portable/'FileBurglar.exe',scratch/'portable-selftest.json')
        assert Path(result['default_data_directory']).resolve()==(portable/'Data').resolve()
        public_result=dict(result)
        public_result['default_data_directory']='Portable application folder/Data'
        (release/'VERIFICATION.json').write_text(json.dumps(public_result,indent=2)+'\n',encoding='utf-8')
        print('Verified portable runtime and data placement',flush=True)
        subprocess.run([str(tool),f'/DPayloadDir={portable}',f'/DBuildVersion={APP_VERSION}',
                        f'/O{assets}',str(ROOT/'installer/FileBurglar.iss')],check=True)
        installer=assets/f'FileBurglar-{APP_VERSION}-windows-x64-setup.exe'
        assert installer.is_file()
        print('Installer compiled',flush=True)
        archive_7z(portable,assets/f'{portable.name}.7z')
        source_name=f'FileBurglar-{APP_VERSION}-source'
        source=scratch/source_name
        shutil.copytree(repo,source)
        archive_7z(source,assets/f'{source_name}.7z')
        files=sorted(p for p in assets.iterdir() if p.suffix in ('.exe','.7z'))
        checks={p.name:digest(p) for p in files}
        for path in files:
            path.with_name(path.name+'.sha256').write_text(f'{checks[path.name]}  {path.name}\n',encoding='ascii')
        (assets/'SHA256SUMS.txt').write_text(''.join(f'{checks[p.name]}  {p.name}\n' for p in files),encoding='ascii')
        versions={name:importlib.metadata.version(name) for name in ('PyInstaller','PyQt6','PyQt6-Qt6','PyQt6_sip','Pillow')}
        manifest={'application':'File Burglar','version':APP_VERSION,'platform':'windows-x64',
                  'python':platform.python_version(),'dependencies':versions,'unsigned':True,
                  'personal_data_included':False,'source_files':len(clean_files(repo)),
                  'portable_files':len(clean_files(portable)),
                  'assets':{p.name:{'bytes':p.stat().st_size,'sha256':checks[p.name]} for p in files}}
        (release/'BUILD-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        shutil.copy2(ROOT/'docs/RELEASE-NOTES.md',release/'RELEASE-NOTES.md')
        (release/'UPLOAD-GUIDE.md').write_text(
            '# File Burglar initial Windows release\n\n'
            '1. Upload the contents of Repository to the root of snuffy420/FileBurglar.\n'
            '2. Enable GitHub Pages from main / (root) for docs/contact.html.\n'
            f'3. Create release v{APP_VERSION}; paste RELEASE-NOTES.md into its description.\n'
            '4. Attach every file in Release-Assets. Portable contains the extracted app for inspection.\n\n'
            'The contact page uses your existing Formspree endpoint. No messages were submitted during testing.\n'
            'Your private index, local settings, logs, and development backups are excluded.\n',encoding='utf-8')
        print(json.dumps(manifest,indent=2))
        print('UPLOAD FOLDER:',release,flush=True)
        return release
    finally:
        assert scratch.parent.resolve()==scratch_root.resolve() and scratch.name.startswith('package-')
        shutil.rmtree(scratch)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    package(args.payload,args.output)
