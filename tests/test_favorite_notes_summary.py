import json,sqlite3,unittest
from unittest.mock import patch
from runtime_fixture import Runtime22Tests,app
from ai_repository import AIRepository
from database import Database
from ui import ArticleDialog
from PySide6.QtWidgets import QMessageBox

class Tests(unittest.TestCase):
    setUp=Runtime22Tests.setUp
    tearDown=Runtime22Tests.tearDown

    def prepare(self):
        self.db.store_refresh_results(self.profile,[(self.row,1)],'pubmed')
        self.db.set_favorite(self.row['article_key'],True,profile_id=self.profile)
        self.repository=AIRepository(self.db)
        self.summary=dict(text='Saved AI summary',presentation={'scientific_question':'Fixture question'},diagram_generation={'artifact':'fixture diagram'})
        self.repository.save_summary(self.row['article_key'],self.summary,'profile:'+str(self.profile))

    def state(self):
        with self.db.connect() as c:
            return dict(favorite=dict(c.execute('SELECT * FROM favorites WHERE article_key=?',(self.row['article_key'],)).fetchone()),
                links=[tuple(r) for r in c.execute('SELECT * FROM ai_summary_links ORDER BY context')],
                groups=[tuple(r) for r in c.execute('SELECT * FROM favorite_group_articles ORDER BY group_id')])

    def test_repeated_notes_save_preserves_favorite_summary_pdf_and_memberships(self):
        self.prepare();copied=self.db.link_pdf(self.row['article_key'],self.pdf);original=copied.read_bytes()
        before=self.state();epoch=self.repository.epoch(self.row['article_key'])
        for tags,note in [('DNA repair,核酸','DNA repair\n中文笔记 🧬'),('new tag','edited note'),('','')]:
            self.db.save_favorite_notes(self.row['article_key'],tags,note)
            saved=self.state();self.assertEqual(saved['favorite']['tags'],tags);self.assertEqual(saved['favorite']['personal_note'],note)
            self.assertEqual(saved['favorite']['saved_at'],before['favorite']['saved_at']);self.assertEqual(saved['favorite']['is_favorite'],1)
            self.assertEqual(saved['links'],before['links']);self.assertEqual(saved['groups'],before['groups'])
            self.assertEqual(self.repository.summary(self.row['article_key']),self.summary)
            self.assertEqual(self.repository.epoch(self.row['article_key']),epoch);self.assertEqual(copied.read_bytes(),original)

    def test_existing_library_reinstalls_legacy_triggers_without_losing_results(self):
        self.prepare()
        with self.db.connect() as c:
            for operation,suffix in [('INSERT','insert'),('UPDATE OF is_favorite','update')]:
                c.execute('DROP TRIGGER ai_keep_favorite_'+suffix)
                c.execute(f'''CREATE TRIGGER ai_keep_favorite_{suffix} AFTER {operation} ON favorites WHEN NEW.is_favorite=1 BEGIN
                    INSERT OR IGNORE INTO ai_summary_links SELECT article_key,'favorites',NULL FROM ai_summaries WHERE article_key=NEW.article_key; END''')
        before=self.state();reopened=Database(self.db.path)
        reopened.save_favorite_notes(self.row['article_key'],'upgraded','saved on upgraded library')
        # The formerly failing UPSERT path must also be safe outside notes.
        for _ in range(3):reopened.set_favorite(self.row['article_key'],True,profile_id=self.profile)
        self.assertEqual(self.state()['links'],before['links']);self.assertEqual(AIRepository(reopened).summary(self.row['article_key']),self.summary)

    def test_note_edit_does_not_rewrite_favorite_state(self):
        self.prepare()
        with self.db.connect() as c:
            c.execute('CREATE TABLE note_test_events(value INTEGER)')
            c.execute('CREATE TRIGGER note_test AFTER UPDATE OF is_favorite ON favorites BEGIN INSERT INTO note_test_events VALUES(1); END')
        self.db.save_favorite_notes(self.row['article_key'],'tag','note')
        with self.db.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM note_test_events').fetchone()[0],0)

    def test_article_dialog_saves_successfully_and_keeps_inputs_on_database_error(self):
        self.prepare();row=self.db.article_record(self.row['article_key']);row['is_favorite']=1
        dialog=ArticleDialog(self.db,row,allow_pdf=True)
        dialog.tags.setText('DNA repair');dialog.note.setPlainText('Research note')
        with patch.object(QMessageBox,'information') as success:
            dialog.save_notes_button.click();success.assert_called_once()
        self.assertEqual(self.state()['favorite']['personal_note'],'Research note')
        dialog.note.setPlainText('Unsaved note')
        with patch.object(self.db,'save_favorite_notes',side_effect=sqlite3.OperationalError('database is locked')),patch.object(QMessageBox,'warning') as warning,patch.object(QMessageBox,'information') as success:
            dialog._save_notes();warning.assert_called_once();success.assert_not_called()
        self.assertEqual(dialog.note.toPlainText(),'Unsaved note');self.assertEqual(row['personal_note'],'Research note')
        dialog.close()

if __name__=='__main__':unittest.main()
