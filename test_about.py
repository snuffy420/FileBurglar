import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch
from PyQt6.QtCore import QTimer, QEvent
from PyQt6.QtWidgets import QApplication

import storage
import ui
from appinfo import APP_VERSION
from about_dialog import AboutDialog, DONATION_BTC_ADDRESS, DONATION_BTC_URI, CONTACT_URL
from engine import Engine


class AboutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.base=Path(__file__).resolve().parent/('about-test-'+uuid.uuid4().hex)
        self.base.mkdir()
        self.data=patch('storage.DATA_DIR',str(self.base));self.data.start()
        db=str(self.base/'test.db');storage.init(db)
        self.engine=Engine(db)
        with patch.object(ui.MainWindow,'_poll_smart',lambda self:None):
            self.window=ui.MainWindow(self.engine,db)
        self.dialog=AboutDialog(self.window)

    def tearDown(self):
        self.dialog.accept()
        self.window.timer.stop();self.window.smart_timer.stop();self.window.con.close()
        self.window.deleteLater();self.dialog.deleteLater();self.app.processEvents()
        self.data.stop()
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('about-test-')
        shutil.rmtree(self.base)

    def test_tabs_version_help_and_contact(self):
        self.assertEqual([self.dialog.tabs.tabText(i) for i in range(3)],['About','Help','Donate BTC'])
        self.assertIn(APP_VERSION,self.dialog.about_content.toPlainText())
        self.assertIn(CONTACT_URL,self.dialog.about_content.toHtml())
        self.assertIn('no timer',self.dialog.help_content.toPlainText())
        self.assertIn('Data beside the executable',self.dialog.help_content.toPlainText())

    def test_exact_owner_address_and_bundled_qr(self):
        self.assertEqual(DONATION_BTC_ADDRESS,'bc1qz8877p6vusc22qrctdj4h7w0uk2l9l9h8lwphy')
        self.assertEqual(DONATION_BTC_URI,'bitcoin:'+DONATION_BTC_ADDRESS)
        self.assertTrue(self.dialog.address.isReadOnly())
        self.assertEqual(self.dialog.address.text(),DONATION_BTC_ADDRESS)
        self.assertFalse(self.dialog.qr_label.pixmap().isNull())

    def test_copy_button_copies_only_the_address(self):
        clipboard=Mock()
        with patch('about_dialog.QApplication') as application:
            application.clipboard.return_value=clipboard
            self.dialog.copy_button.click()
        clipboard.setText.assert_called_once_with(DONATION_BTC_ADDRESS)
        self.assertIn('copied',self.dialog.copy_status.text())

    def test_main_about_button_opens_dialog_without_starting_monitoring(self):
        opened=[]
        def close_dialog():
            modal=self.app.activeModalWidget()
            opened.append(modal)
            if modal is not None:modal.accept()
        QTimer.singleShot(0,close_dialog)
        self.window.about_btn.click()
        self.assertEqual(len(opened),1)
        self.assertIsInstance(opened[0],AboutDialog)
        self.assertFalse(self.engine.is_alive())
        self.assertIsNone(self.engine.folder_updater.ident)
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertEqual(len(self.window.findChildren(AboutDialog)),1)


if __name__=='__main__':unittest.main()
