"""Exercise desktop form/actions without requiring a display server.

Native widget rendering still needs a desktop launch; these tests check that
form values and selected records reach the real SQLite inventory correctly.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import HomebaseApp, ItemDialog, LocationDialog, main
from inventory import Inventory


class Value:
    def __init__(self, value=''):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class DesktopLogicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inventory = Inventory(Path(self.temp.name) / 'inventory.sqlite3')
        self.app = HomebaseApp.__new__(HomebaseApp)
        self.app.inventory = self.inventory
        self.app.root = Mock()
        self.app.root.grab_current.return_value = None
        self.app.notebook = Mock()
        self.app.inventory_tab = object()
        self.app.refresh = Mock()
        self.app.set_status = Mock()
        self.app.clear_filters = Mock()
        self.app.undo_button = Mock()
        self.app.selected_id = 'item_1'
        self.app.deleted_item = None

    def tearDown(self):
        self.inventory.close()
        self.temp.cleanup()

    def form(self, item_id=None):
        dialog = ItemDialog.__new__(ItemDialog)
        dialog.app = self.app
        dialog.item_id = item_id
        dialog.photo = b''
        dialog.photo_name = ''
        dialog.values = {key: Value(value) for key, value in dict(name='Desktop added cable', category='Electronics', quantity='3', value='15.25', low_stock='1', tags='usb-c, spare').items()}
        dialog.location_ids = {'Home → Office → Oak desk → Cable drawer': 'cables'}
        dialog.location_value = Value('Home → Office → Oak desk → Cable drawer')
        dialog.notes = Mock()
        dialog.notes.get.return_value = 'Saved from desktop form'
        dialog.error = Value()
        dialog.window = Mock()
        return dialog

    def test_item_form_add_then_edit_and_move(self):
        dialog = self.form()
        dialog.save()
        saved_id = self.app.selected_id
        saved = self.inventory.get_item(saved_id)
        self.assertEqual(saved['name'], 'Desktop added cable')
        self.assertEqual(saved['quantity'], 3)
        self.assertEqual(saved['location_id'], 'cables')
        dialog.window.destroy.assert_called_once()
        self.app.notebook.select.assert_called_with(self.app.inventory_tab)
        dialog = self.form(saved_id)
        dialog.values['name'].set('Moved from desktop form')
        dialog.location_ids = {'Gear shelf': 'gear'}
        dialog.location_value.set('Gear shelf')
        dialog.save()
        self.assertEqual(self.inventory.get_item(saved_id)['location_id'], 'gear')
        self.assertEqual(self.inventory.get_item(saved_id)['name'], 'Moved from desktop form')

    def test_invalid_item_form_stays_open_without_database_changes(self):
        dialog = self.form()
        dialog.values['quantity'].set('-1')
        before = self.inventory.stats()
        dialog.save()
        self.assertIn('whole number', dialog.error.get())
        dialog.window.destroy.assert_not_called()
        self.assertEqual(self.inventory.stats(), before)

    def test_location_form_parent_and_rename_reach_database(self):
        dialog = LocationDialog.__new__(LocationDialog)
        dialog.app, dialog.location_id = self.app, None
        dialog.name, dialog.kind, dialog.parent = Value('Bottom drawer'), Value('Drawer'), Value('Oak desk')
        dialog.parents = {'Oak desk': 'desk'}
        dialog.error, dialog.window = Value(), Mock()
        dialog.save()
        location = next(loc for loc in self.inventory.locations() if loc['name'] == 'Bottom drawer')
        self.assertEqual(location['parent_id'], 'desk')
        dialog.location_id = location['id']
        dialog.name.set('Tiny drawer')
        dialog.save()
        self.assertEqual(self.inventory.get_location(location['id'])['name'], 'Tiny drawer')

    def test_delete_confirmation_cancel_delete_and_undo(self):
        with patch('app.messagebox.askyesno', return_value=False):
            self.app.delete_item()
        self.assertEqual(self.inventory.get_item('item_1')['name'], 'Sony WH-1000XM4')
        with patch('app.messagebox.askyesno', return_value=True):
            self.app.delete_item()
        self.assertEqual(self.app.deleted_item['id'], 'item_1')
        self.assertEqual(self.inventory.stats()['items'], 17)
        self.app.undo_delete()
        self.assertEqual(self.inventory.stats()['items'], 18)
        self.assertEqual(self.app.selected_id, 'item_1')
        self.assertIsNone(self.app.deleted_item)

    def test_quantity_buttons_use_selected_item_and_persist(self):
        self.app.adjust_quantity(1)
        self.assertEqual(self.inventory.get_item('item_1')['quantity'], 2)
        self.app.refresh.assert_called_once()
        self.app.adjust_quantity(-1)
        self.assertEqual(self.inventory.get_item('item_1')['quantity'], 1)

    def test_database_check_entry_point_runs_without_a_display(self):
        path = Path(self.temp.name) / 'check.sqlite3'
        with patch('builtins.print') as output:
            result = main(['--check', '--db', str(path)])
        self.assertEqual(result, 0)
        self.assertIn('18 items, 79 units, 16 locations', output.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
