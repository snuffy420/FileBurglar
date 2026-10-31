"""Optional features, on-demand reports, and background UI coordination."""
import copy
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject,Qt,QTimer,QUrl,pyqtSignal
from PyQt6.QtGui import QAction,QDesktopServices
from PyQt6.QtWidgets import (QApplication,QCheckBox,QComboBox,QFileDialog,QFormLayout,
    QHBoxLayout,QInputDialog,QLabel,QLineEdit,QMenu,QMessageBox,QPlainTextEdit,QPushButton,
    QProgressDialog,QScrollArea,QSpinBox,QTabWidget,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

import notifications
import reports
import settings
import smart
import storage
import updates
import work_mode
import volumes
from appinfo import APP_NAME,APP_VERSION
from ui import AppDialog,ORANGE,NEON,RED,TEXT,SortItem,cell,fill,human_bytes,make_table,monitoring_label,signed_bytes


class Preferences(AppDialog):
    def __init__(self,parent,values):
        super().__init__(parent,'Options',width=760)
        self.resize(800,680)
        self.values=copy.deepcopy(values)
        tabs=QTabWidget();self.body.addWidget(tabs,1)
        def page(title):
            area=QScrollArea();area.setWidgetResizable(True)
            widget=QWidget();layout=QVBoxLayout(widget);layout.setSpacing(12)
            area.setWidget(widget);tabs.addTab(area,title.replace('&','&&'))  # show a literal &, not a shortcut marker
            return layout
        self.checks={}
        def check(layout,key,title):
            box=QCheckBox(title);box.setChecked(values[key]);self.checks[key]=box;layout.addWidget(box)
            return box
        health=page('Temperature')
        enabled=check(health,'health_enabled','Enable drive-health and temperature monitoring')
        alerts=check(health,'temperature_alerts','Enable temperature notifications')
        form=QFormLayout();self.spins={}
        for kind in ('internal','external'):
            for key in ('warm','hot'):
                spin=QSpinBox();spin.setRange(20,100);spin.setValue(int(values[kind][key]));self.spins[kind,key]=spin
                form.addRow(f'{kind.title()} drives: {key} at (°C)',spin)
        self.reset=QComboBox();self.reset.addItem('1 hour',1);self.reset.addItem('2 hours',2)
        self.reset.setCurrentIndex(values['alert_reset_hours']-1)
        form.addRow('Reset after sustained cooling',self.reset);health.addLayout(form)
        text=QLabel('One warm notification, then one hot escalation per heat episode. Alerts reset only after the selected time at least 3°C below warm.');text.setWordWrap(True);health.addWidget(text)
        def enabled_changed(value):
            alerts.setEnabled(value);self.reset.setEnabled(value)
            for spin in self.spins.values():spin.setEnabled(value)
        enabled.toggled.connect(enabled_changed);enabled_changed(enabled.isChecked());health.addStretch()
        tracking=page('Tracking')
        check(tracking,'game_mode','Game/work mode: defer heavy background work')
        check(tracking,'auto_game_mode','Enable game/work mode for these applications')
        self.processes=QLineEdit(', '.join(values['game_processes']));self.processes.setPlaceholderText('game.exe, blender.exe, application.exe')
        tracking.addWidget(self.processes)
        tracking.addWidget(QLabel('Excluded folders — one full folder path per line'))
        self.exclusions=QPlainTextEdit('\n'.join(values['exclusions']));self.exclusions.setMinimumHeight(160);tracking.addWidget(self.exclusions)
        self.exclusions.setPlaceholderText('No excluded folders. Add a folder below, or enter one full folder path per line.')
        self.exclusions.setMaximumHeight(240)
        browse=QPushButton('Add excluded folder…');browse.clicked.connect(self.add_exclusion)
        folder_actions=QHBoxLayout();folder_actions.addWidget(browse);folder_actions.addStretch();tracking.addLayout(folder_actions)
        note=QLabel('Excluded folders are removed from tracking and reports, not from your disk. Re-including one creates a targeted baseline. Startup scans retain their existing fast mode.');note.setWordWrap(True);tracking.addWidget(note);tracking.addStretch()
        space=page('Space alerts')
        space.addWidget(QLabel('Low free space (leave empty to disable). Values are GiB or percent.'))
        self.space=QTableWidget(0,3);self.space.setHorizontalHeaderLabels(['Drive (e.g. D:)','Minimum free','Unit: GB or %'])
        self.space.horizontalHeader().setStretchLastSection(True);space.addWidget(self.space)
        for rule in values['space_rules']:self.add_row(self.space,[rule.get('drive',''),rule.get('value',10),rule.get('unit','GB')])
        self.rule_buttons(space,self.space,['D:','10','GB'])
        space.addWidget(QLabel('Rapid folder growth (leave empty to disable). Files directly and indirectly inside the folder.'))
        self.growth=QTableWidget(0,3);self.growth.setHorizontalHeaderLabels(['Full folder path','Growth (GiB)','Within hours'])
        self.growth.horizontalHeader().setStretchLastSection(True);space.addWidget(self.growth)
        for rule in values['growth_rules']:self.add_row(self.growth,[rule.get('path',''),rule.get('gb',1),rule.get('hours',1)])
        self.rule_buttons(space,self.growth,['','1','1'])
        note=QLabel('Space/growth notifications are optional, deduplicated, and deferred during game/work mode. Re-arm requires an hour below the recovery threshold.');note.setWordWrap(True);space.addWidget(note)
        misc=page('Updates & reconnects')
        check(misc,'auto_updates','Check for stable GitHub releases once daily')
        check(misc,'reconnect_summaries','Notify once after an external drive reconnects')
        misc.addWidget(QLabel('Updates are announced only. Downloads and installation remain under your control.'))
        misc.addStretch()
        buttons=QHBoxLayout();buttons.addStretch()
        cancel=QPushButton('Cancel');cancel.clicked.connect(self.reject);buttons.addWidget(cancel)
        save=QPushButton('Save');save.clicked.connect(self.save);buttons.addWidget(save);self.body.addLayout(buttons)

    @staticmethod
    def add_row(table,values):
        row=table.rowCount();table.insertRow(row)
        for col,value in enumerate(values):table.setItem(row,col,QTableWidgetItem(str(value)))

    def rule_buttons(self,layout,table,defaults):
        table.setMinimumHeight(130);table.setMaximumHeight(150)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        from PyQt6.QtWidgets import QHeaderView
        table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(1,150);table.setColumnWidth(2,150)
        row=QHBoxLayout()
        add=QPushButton('Add rule');add.clicked.connect(lambda:self.add_row(table,defaults));row.addWidget(add)
        remove=QPushButton('Remove selected');remove.clicked.connect(lambda:table.removeRow(table.currentRow()));row.addWidget(remove)
        row.addStretch();layout.addLayout(row)

    def add_exclusion(self):
        path=QFileDialog.getExistingDirectory(self,'Exclude folder')
        if path:self.exclusions.appendPlainText(os.path.normpath(path))

    def save(self):
        values=copy.deepcopy(self.values)
        for key,box in self.checks.items():values[key]=box.isChecked()
        for (kind,key),spin in self.spins.items():values[kind][key]=spin.value()
        if any(values[k]['warm']>=values[k]['hot'] for k in ('internal','external')):
            QMessageBox.warning(self,'Options','Each hot limit must exceed its warm limit.');return
        values['alert_reset_hours']=self.reset.currentData()
        values['game_processes']=[x.strip() for x in self.processes.text().split(',') if x.strip()][:64]
        values['exclusions']=list(dict.fromkeys(os.path.normpath(x.strip()) for x in self.exclusions.toPlainText().splitlines() if x.strip()))[:256]
        if any(not os.path.isabs(x) for x in values['exclusions']):
            QMessageBox.warning(self,'Options','Use full absolute paths for excluded folders.');return
        try:
            values['space_rules']=[];values['growth_rules']=[]
            for row in range(min(self.space.rowCount(),64)):
                drive,value,unit=[self.space.item(row,c).text().strip() if self.space.item(row,c) else '' for c in range(3)]
                drive=drive.upper();unit=unit.upper();value=float(value)
                if len(drive)!=2 or drive[1]!=':' or not drive[0].isascii() or not drive[0].isalpha() or unit not in ('GB','%') or not 0<value<=100000 or (unit=='%' and value>100):raise ValueError()
                values['space_rules'].append(dict(drive=drive,value=value,unit=unit))
            for row in range(min(self.growth.rowCount(),64)):
                path,gb,hours=[self.growth.item(row,c).text().strip() if self.growth.item(row,c) else '' for c in range(3)]
                gb=float(gb);hours=float(hours)
                if not os.path.isabs(path) or not 0<gb<=100000 or not 0<hours<=720:raise ValueError()
                values['growth_rules'].append(dict(path=os.path.normpath(path),gb=gb,hours=hours))
        except (ValueError,OverflowError):
            QMessageBox.warning(self,'Options','Check rule paths, drive letters, positive numeric limits, and GB/% units.');return
        self.values=values;self.accept()


class FeatureController(QObject):
    completed=pyqtSignal(str,object)
    def __init__(self,window,db):
        super().__init__(window)
        self.window=window;self.db=db;self.manager=None;self.busy=set();self.cancel=threading.Event();self.closing=False
        self.episodes=notifications.Episodes()
        self.update_store=notifications.Episodes('update-state.json')
        self.update_state=self.update_store.entries
        self.last_process=0;self.last_space=0;self.last_growth=0;self.last_view=0;self.auto_process=None
        self.measurement_idle=threading.Event();self.measurement_idle.set()
        self.measure_all=False
        self.capacities={}
        self.completed.connect(self.receive)
        self.work_button=QPushButton('Game/work mode');self.work_button.setCheckable(True)
        self.work_button.setChecked(window._limits['game_mode']);self.work_button.toggled.connect(self.manual_work)
        row=QHBoxLayout();row.addWidget(self.work_button)
        self.notice=QLabel('');self.notice.setWordWrap(True);row.addWidget(self.notice,1)
        self.available_release=None
        self.update_button=QPushButton('Check for updates');self.update_button.clicked.connect(self.update_clicked);row.addWidget(self.update_button)
        cached=self.update_state.get('release')
        try:
            if isinstance(cached,dict) and self.update_state.get('skip')!=cached.get('version') and time.time()>=self.update_state.get('remind_after',0):
                validated=updates.validate_release(dict(tag_name=cached['version'],html_url=cached['url']),APP_VERSION)
                if validated:
                    self.available_release=validated;self.update_button.setText('Update '+validated['version']+' available')
        except (KeyError,ValueError,TypeError):pass
        self.export_button=QPushButton('Export CSV');self.export_button.clicked.connect(self.export);row.addWidget(self.export_button)
        window.centralWidget().layout().insertLayout(4,row)
        page=QWidget();layout=QVBoxLayout(page);filters=QHBoxLayout()
        self.period=QComboBox()
        for label,hours in (('Last hour',1),('Last day',24),('Last week',168)):self.period.addItem(label,hours)
        self.drive=QComboBox();self.drive.addItem('All drives',None)
        self.period.currentIndexChanged.connect(lambda _:self.refresh_growth(True));self.drive.currentIndexChanged.connect(lambda _:self.refresh_growth(True))
        filters.addWidget(self.period);filters.addWidget(self.drive);filters.addStretch();layout.addLayout(filters)
        self.table=make_table(['Folder','Changes','Written','Freed','Net growth']);self.table.configure_fit([600,90,110,110,120],0)
        self.table.sortItems(4,Qt.SortOrder.DescendingOrder)
        layout.addWidget(self.table)
        hint=QLabel('Indexed activity only. Each file contributes to its direct parent once. Exclusions and unavailable history limit coverage. Largest 300 folders shown; CSV exports the full filtered result.');hint.setWordWrap(True);layout.addWidget(hint)
        window.tabs.addTab(page,'What used my space?')
        for table,column in ((window.report_table,0),(window.log_table,2),(self.table,0)):
            self.copy_menu(table,column)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick)
        if '--self-test' not in sys.argv and not os.environ.get('FILEBURGLAR_TESTING'):self.timer.start(2000)
        self.started=time.monotonic()
        self.apply_mode()

    def task(self,kind,fn):
        if kind in self.busy or self.closing:return False
        self.busy.add(kind)
        if kind=='space':self.measurement_idle.clear()
        def work():
            try:
                from indexer import lower_thread_priority
                lower_thread_priority()
                result=(True,fn())
            except Exception as error:result=(False,str(error))
            finally:
                if kind=='space':self.measurement_idle.set()
            if not self.closing:
                try:self.completed.emit(kind,result)
                except RuntimeError:pass
        threading.Thread(target=work,name='fb-'+kind,daemon=True).start()
        return True

    def stop(self):
        self.closing=True;self.timer.stop();self.cancel.set();smart.block('shutdown')

    def copy_menu(self,table,column):
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        def menu(pos):
            selected=table.selectedItems()
            rows=sorted({x.row() for x in selected})
            if not rows:return
            popup=QMenu(table);action=popup.addAction('Copy full path'+('s' if len(rows)>1 else ''))
            action.triggered.connect(lambda:QApplication.clipboard().setText('\n'.join(table.item(r,column).text() for r in rows if table.item(r,column))))
            popup.exec(table.viewport().mapToGlobal(pos))
        table.customContextMenuRequested.connect(menu)

    def manual_work(self,value):
        self.window._limits['game_mode']=value;settings.save(self.window._limits);self.apply_mode()

    def apply_mode(self):
        enabled=self.window._limits['game_mode'] or bool(self.auto_process)
        self.window.engine.set_work_mode(enabled)
        self.work_button.setText('Game/work mode: ON' if enabled else 'Game/work mode')
        self.work_button.setToolTip('Heavy work deferred'+(': '+self.auto_process if self.auto_process else '') if enabled else 'Collect changes while deferring heavy background work')

    def options(self):
        old=copy.deepcopy(self.window._limits)
        dialog=Preferences(self.window,old)
        try:
            if not dialog.exec():return
            settings.save(dialog.values)
            new=settings.load();self.window._limits=new
            self.window._health_generation+=1
            if new['health_enabled']:
                smart.unblock('options');self.window.smart_timer.start(60000)
            else:
                smart.block('options');self.window.smart_timer.stop();self.window._disks={}
                self.episodes.cooling.clear()
                for toast in list(self.window._toasts):toast._close_now()
            if new['exclusions']!=old['exclusions']:
                self.window.engine.configure_exclusions(new['exclusions'],old['exclusions'])
                self.notice.setText('Updating tracking coverage for exclusions…')
            self.work_button.setChecked(new['game_mode']);self.apply_mode()
            if not new['auto_game_mode']:self.auto_process=None;self.apply_mode()
            self.window.drives_changed.emit();self.window.refresh()
            if not new['temperature_alerts']:
                for toast in list(self.window._toasts):toast._close_now()
            if new['health_enabled']:self.window._poll_smart()
        finally:dialog.deleteLater()

    def tick(self):
        if self.closing:return
        window=self.window;limits=window._limits;now=time.monotonic()
        tray=getattr(window,'tray',None)
        if tray:
            snap=window.engine.snapshot()
            tray.icon.setToolTip('\n'.join([APP_NAME]+[r[:2]+': '+monitoring_label(r,s,window.engine) for r,s in sorted(snap.items())]))
        # No drive or history work in this lightweight two-second UI tick.
        if hasattr(window.engine,'notices'):
            while True:
                try:kind,root,summary=window.engine.notices.get_nowait()
                except queue.Empty:break
                if kind=='mounts':
                    window._limits=settings.load()
                    window._health_generation+=1
                    if root:window._disks.pop(root[:2],None)
                    else:window._disks={}
                    self.capacities.pop(root,None)
                    window.drives_changed.emit()
                    if window.explorer:
                        window.explorer._cache.clear();window.explorer._navigate(None)
                    window.refresh();window._poll_smart()
                    continue
                if kind=='identity_error':
                    self.notice.setText('Could not associate '+root+' with its saved volume: '+str(summary));continue
                if kind=='reconnect' and limits['reconnect_summaries']:
                    device=self.manager.devices.get(root,{}) if self.manager else {}
                    if device.get('removable'):
                        added,removed,changed,delta=summary
                        self.notify('Drive reconnected',f'{root[:2]}: {added:,} added, {removed:,} removed, {changed:,} changed; net {signed_bytes(delta)}. Snapshot comparison, not a complete edit history.')
        if limits['auto_game_mode'] and now-self.last_process>=30:
            self.last_process=now;self.task('process',lambda:work_mode.matched_process(limits['game_processes']))
        if now-self.started>=15 and limits['auto_updates'] and time.time()-self.update_state.get('checked',0)>=86400:
            self.check_updates(False)
        visible=window.isVisible() and not window.isMinimized()
        if not window.engine.work_mode and now-self.last_space>=60 and (visible or limits['space_rules'] or limits['growth_rules']):
            self.last_space=now
            self.measure_all=visible
            include_growth=now-self.last_growth>=300
            if include_growth:self.last_growth=now
            self.task('space',lambda:self.space_observations(include_growth))
        if window.isVisible() and not window.isMinimized() and window.tabs.currentIndex()==3:self.refresh_growth()

    def notify(self,title,text):
        if self.closing:return
        self.notice.setText(title+': '+text)
        tray=getattr(self.window,'tray',None)
        if tray:tray.icon.showMessage(title,text)

    def check_updates(self,manual=False):
        if 'update' in self.busy:return
        self.update_manual=manual
        self.update_state['checked']=time.time();self.update_store.save()
        self.update_button.setEnabled(False)
        self.task('update',lambda:updates.check(APP_VERSION,None if manual else self.update_state.get('etag')))

    def update_clicked(self):
        if self.available_release:self.offer_update(self.available_release)
        else:self.check_updates(True)

    def offer_update(self,release):
        version=release['version']
        dialog=QMessageBox(self.window);dialog.setWindowTitle('File Burglar update');dialog.setText('Version '+version+' is available. Installation is your choice.')
        view=dialog.addButton('View release / download',QMessageBox.ButtonRole.AcceptRole)
        later=dialog.addButton('Remind me tomorrow',QMessageBox.ButtonRole.ActionRole)
        skip=dialog.addButton('Skip this version',QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton()==view:QDesktopServices.openUrl(QUrl(release['url']))
        elif dialog.clickedButton()==later:
            self.update_state['remind_after']=time.time()+86400;self.update_state.pop('notified',None)
        elif dialog.clickedButton()==skip:self.update_state['skip']=version
        if dialog.clickedButton() in (later,skip):
            self.available_release=None;self.update_button.setText('Check for updates')
        self.update_store.save()

    def refresh_growth(self,force=False):
        now=time.monotonic()
        if not force and now-self.last_view<60:return
        self.last_view=now
        snapshot=self.window.engine.snapshot()
        current=self.drive.currentData()
        self.drive.blockSignals(True)
        self.drive.clear();self.drive.addItem('All drives',None)
        for root,s in sorted(snapshot.items()):self.drive.addItem(volumes.display_root(dict(s,namespace=root,present=s['state']!='offline')),s.get('volume_id') or root.rstrip('\\'))
        self.drive.setCurrentIndex(max(0,self.drive.findData(current)));self.drive.blockSignals(False)
        selected=storage.filter_root(self.window.con,self.drive.currentData())
        sql,args=reports.growth_query(self.period.currentData(),selected.rstrip('\\') if selected else None,volume_id=storage.filter_identity(self.window.con,self.drive.currentData()))
        def query():
            con=reports.readonly(self.db)
            try:reports.prepare(con);return con.execute(sql,args).fetchall(),volumes.rows(con)
            finally:con.close()
        self.task('growth',query)

    def space_observations(self,include_growth=True):
        limits=copy.deepcopy(self.window._limits);snap=self.window.engine.snapshot();result=[];capacities={}
        captured={r:m['id'] for r,m in self.window.engine.mounts.items()}
        def valid(root):return self.window.engine._valid_mount(root) and captured.get(root)==self.window.engine.mounts.get(root,{}).get('id')
        import shutil
        wanted=set(snap) if self.measure_all else {rule.get('drive','')+'\\' for rule in limits['space_rules']}
        for root in wanted:
            if snap.get(root,{}).get('state') not in ('ready','indexing','unmonitored') or root in self.window.engine.released_roots or not valid(root):continue
            try:
                usage=shutil.disk_usage(root)
                if valid(root):capacities[root]=usage
            except OSError:pass
        for rule in limits['space_rules']:
            root=rule.get('drive','')+'\\'
            if snap.get(root,{}).get('state') not in ('ready','indexing'):continue
            if root in self.window.engine.released_roots:continue
            try:
                usage=capacities[root];value=usage.free/usage.total*100 if rule.get('unit')=='%' else usage.free/1024**3
                threshold=float(rule['value'])
                severity=2 if value<threshold/2 else 1 if value<threshold else 0
                result.append(('space:'+str(captured.get(root,root)), severity,value>threshold*1.2,f'{root[:2]} has {human_bytes(usage.free)} free.'))
            except (OSError,ValueError,KeyError,ZeroDivisionError):continue
        con=reports.readonly(self.db)
        try:
            con.set_progress_handler(lambda:int(self.window.engine.work_mode or self.closing),1000)
            for rule in limits['growth_rules'] if include_growth else ():
                try:
                    path=rule['path'];hours=float(rule['hours']);threshold=float(rule['gb'])*1024**3
                    root=os.path.splitdrive(path)[0]+'\\'
                    if snap.get(root,{}).get('state') not in ('ready','indexing') or root in self.window.engine.released_roots or storage.is_excluded_path(path) or not valid(root):continue
                    lo=path.rstrip('\\')+'\\';hi=path.rstrip('\\')+']'
                    delta=con.execute('SELECT COALESCE(SUM(delta),0) FROM file_log WHERE ts>=? AND path>=? AND path<?',(time.time()-hours*3600,lo,hi)).fetchone()[0]
                    severity=2 if delta>=threshold*2 else 1 if delta>=threshold else 0
                    if valid(root):result.append(('growth:'+str(captured.get(root,root))+':'+path[len(root):],severity,delta<threshold*.8,f'{path} grew by {human_bytes(delta)} in {hours:g} hour(s).'))
                except (ValueError,KeyError):continue
        finally:con.close()
        return result,include_growth,capacities

    def export(self):
        index=self.window.tabs.currentIndex()
        if index not in (1,2,3):
            self.notice.setText('Open Folder report, Activity log, or What used my space? to export its filtered results.');return
        if 'export' in self.busy:return
        destination,_=QFileDialog.getSaveFileName(self.window,'Export all filtered rows','','CSV files (*.csv)')
        if not destination:return
        sql,args,headers=self.export_query(index)
        self.cancel=threading.Event()
        self.progress=QProgressDialog('Exporting all filtered rows…','Cancel',0,0,self.window)
        self.progress.setWindowTitle('Export CSV');self.progress.canceled.connect(self.cancel.set);self.progress.show()
        self.task('export',lambda:reports.export(self.db,sql,args,headers,destination,self.cancel))

    def export_query(self,index):
        w=self.window
        if index==3:
            root=storage.filter_root(w.con,self.drive.currentData())
            sql,args=reports.growth_query(self.period.currentData(),root.rstrip('\\') if root else None,limit=None,volume_id=storage.filter_identity(w.con,self.drive.currentData()))
            # Drop the default ORDER BY before applying the current table sort.
            sql=sql.rsplit(' ORDER BY ',1)[0]
            table=self.table;columns=['folder','events','written','freed','net'];headers=['Folder','Changes','Written bytes','Freed bytes','Net bytes']
        elif index==1:
            sql='SELECT folder,SUM(events),SUM(added),SUM(freed),MAX(last_ts) FROM folder_daily WHERE day>=?';args=[storage.day_start(w.period.currentData())]
            if w.report_drive.currentData():
                identity=storage.filter_identity(w.con,w.report_drive.currentData())
                sql+=' AND volume_id=?' if identity else ' AND drive=?'
                args.append(identity or storage.filter_root(w.con,w.report_drive.currentData()).rstrip('\\'))
            sql+=' GROUP BY folder';table=w.report_table;columns=['1','2','3','4','5'];headers=['Folder','Changes','Written bytes','Freed bytes','Last change Unix time']
        else:
            sql="SELECT strftime('%Y-%m-%d %H:%M:%S',ts,'unixepoch','localtime'),COALESCE((SELECT last_root FROM volumes WHERE id=file_log.volume_id),substr(path,1,2)),path,delta,size,action,volume_id,COALESCE(observed_path,observed_root || substr(path,length(COALESCE((SELECT namespace FROM volumes WHERE id=file_log.volume_id),observed_root))+1)),legacy_unverified FROM file_log WHERE 1=1";args=[]
            if w.log_drive.currentData():
                identity=storage.filter_identity(w.con,w.log_drive.currentData())
                if identity:sql+=' AND volume_id=?';args.append(identity)
                else:
                    lo,hi=storage.drive_range(storage.filter_root(w.con,w.log_drive.currentData()));sql+=' AND path>=? AND path<?';args.extend((lo,hi))
            if w.log_action.currentData():sql+=' AND action=?';args.append(w.log_action.currentData())
            if w.log_search.text().strip():sql+=' AND path LIKE ?';args.append('%'+w.log_search.text().strip()+'%')
            table=w.log_table;columns=['ts','2','path','delta','size','action'];headers=['Time','Drive','Path','Change bytes','Size bytes','Action','Volume identity','Observed original path','Historical identity unverified']
        header=table.horizontalHeader();column=header.sortIndicatorSection();direction='DESC' if header.sortIndicatorOrder()==Qt.SortOrder.DescendingOrder else 'ASC'
        sql+=' ORDER BY '+columns[max(0,min(column,len(columns)-1))]+' '+direction
        return sql,args,headers

    def removal(self,root,eject=False,resume=False,expected=None):
        if self.manager is None:
            QMessageBox.information(self.window,'Drive removal','Drive manager is unavailable.');return
        if resume:self.manager.resume(root);return
        group=self.manager.group(root)
        text='Ask Windows to safely eject' if eject else 'Release File Burglar’s access to'
        text+=' '+root+'?'
        if len(group)>1:
            text+='\n\nThis physical device also contains '+', '.join(sorted(group-{root}))+'.\nFile Burglar will release these volumes together.'
        if QMessageBox.question(self.window,'Drive removal',text)!=QMessageBox.StandardButton.Yes:return
        def remove():
            import devices
            if expected and devices.volume_identity(root)!=expected:return False,'The drive assignment changed. Refresh and select the drive again.'
            return self.manager.safe_eject(root,expected) if eject else self.manager.release(root,8)
        self.task('removal',remove)

    def link_saved(self,root,expected=None):
        if not self.manager:return
        destination=self.window.engine.snapshot().get(root,{}).get('volume_id')
        if expected and expected!=destination:self.window.refresh();return
        candidates=[v for v in volumes.rows(self.window.con) if not v['verified'] and v['id']!=destination]
        if not candidates:
            QMessageBox.information(self.window,'Link saved drive','There are no unverified saved drives to link.');return
        labels=[volumes.display_root(v)+' · '+v['id'][-6:] for v in candidates]
        choice,accepted=QInputDialog.getItem(self.window,'Link saved drive','Choose the saved entry that belongs to this physical drive:',labels,0,False)
        if not accepted:return
        source=candidates[labels.index(choice)]['id']
        if QMessageBox.question(self.window,'Confirm drive identity','Only link entries you know belong to the same drive. Link '+choice+' to '+root+'?')!=QMessageBox.StandardButton.Yes:return
        self.notice.setText('Linking saved index and history…')
        def link():
            import devices
            if devices.volume_identity(root)!=destination:raise ValueError('The drive assignment changed. Refresh and select it again.')
            ok,message=self.manager.release(root,8)
            if not ok:raise RuntimeError(message)
            self.window.engine.link_saved_volume(source,destination).result(timeout=120)
            self.manager.resume(root)
            return True,'Saved history linked. Reconciliation will check changes since the saved snapshot.'
        self.task('removal',link)

    def receive(self,kind,result):
        self.busy.discard(kind)
        if self.closing:return
        ok,value=result
        if kind=='update':
            self.update_button.setEnabled(True)
            if not self.update_manual and not self.window._limits['auto_updates']:return
            if not ok:
                if self.update_manual:self.notice.setText('Update check failed. Try again later. '+value)
                return
            release,etag=value
            if release:
                self.update_state['release']=release
            elif etag and etag==self.update_state.get('etag'):
                candidate=self.update_state.get('release')
                if isinstance(candidate,dict) and updates.version(candidate.get('version')) and updates.version(candidate['version'])>updates.version(APP_VERSION):release=candidate
            if etag:self.update_state['etag']=etag;self.update_store.save()
            if not release:
                if self.update_manual:self.notice.setText('No newer stable release is available.')
                return
            version=release['version']
            if not self.update_manual and self.update_state.get('skip')==version:return
            if not self.update_manual and time.time()<self.update_state.get('remind_after',0):return
            self.available_release=release;self.update_button.setText('Update '+version+' available')
            if not self.update_manual and self.update_state.get('notified')==version:return
            self.update_state['notified']=version;self.update_store.save()
            self.notify('Update available','File Burglar '+version+' is available.')
            if self.update_manual:self.offer_update(release)
        elif kind=='growth':
            if ok:
                records,metadata=value
                fill(self.table,[[cell(volumes.display_path(folder,metadata)),SortItem(str(events),events),SortItem(human_bytes(written),written),SortItem(human_bytes(freed),freed),SortItem(signed_bytes(net),net)] for folder,events,written,freed,net in records])
            else:self.notice.setText('Could not load growth report: '+str(value))
        elif kind=='process':
            self.auto_process=value if ok else None;self.apply_mode()
        elif kind=='space' and ok:
            if self.window.engine.work_mode:return
            observations,growth_checked,capacities=value;observed=set()
            self.capacities=capacities
            for root,tile in self.window.tiles.items():
                tile.capacity=capacities.get(root) if root not in self.window.engine.released_roots else None
                if root not in self.window.engine.released_roots:tile.update_info(self.window.engine.snapshot().get(root,{}).get('state','offline'))
            for key,severity,recovered,message in observations:
                observed.add(key)
                if self.episodes.observe(key,severity,recovered,max_gap=660 if key.startswith('growth:') else 180):self.notify('Space alert',message)
            for key in list(self.episodes.cooling):
                if (key.startswith('space:') or (growth_checked and key.startswith('growth:'))) and key not in observed:self.episodes.observe(key,None,False)
        elif kind=='export':
            self.progress.close();self.progress.deleteLater()
            self.notice.setText(f'Exported {value:,} filtered rows.' if ok else 'Export stopped: '+str(value))
        elif kind=='removal':
            success,message=value if ok else (False,value)
            QMessageBox.information(self.window,'Drive removal',message)
            self.window.refresh()
