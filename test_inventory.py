"""Run with: python -m unittest discover -s tests -v"""
import base64
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inventory import Inventory, InventoryError

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aU1sAAAAASUVORK5CYII=')


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'inventory.sqlite3'
        self.inventory = Inventory(self.path)

    def tearDown(self):
        self.inventory.close()
        self.temp.cleanup()

    def new_item(self, **changes):
        data = dict(name='Travel cable', category='Electronics', quantity='3', location_id='cables',
                    value='12.50', low_stock='1', tags='USB-C, travel, usb-c', notes='Braided spare', photo=b'', photo_name='')
        data.update(changes)
        return data

    def test_initial_samples_and_hierarchy(self):
        self.assertEqual(self.inventory.stats(), dict(items=18, units=79, locations=16, low=3, value=self.inventory.stats()['value']))
        self.assertEqual(self.inventory.path_names('cables'), ['Home', 'Office', 'Oak desk', 'Cable drawer'])
        self.assertTrue(all(item['location_id'] for item in self.inventory.items()))

    def test_item_add_search_edit_move_delete_undo_and_reopen(self):
        item = self.inventory.save_item(self.new_item())
        self.assertEqual(item['tags'], ['usb-c', 'travel'])
        self.assertEqual(self.inventory.items(query='braided spare')[0]['id'], item['id'])
        self.assertIn(item['id'], [row['id'] for row in self.inventory.items(location_id='home', tag='travel', category='Electronics')])
        updated = self.inventory.save_item(self.new_item(name='Trip cable', location_id='gear', quantity='5'), item['id'])
        self.assertEqual(updated['quantity'], 5)
        self.assertNotIn(item['id'], [row['id'] for row in self.inventory.items(location_id='office')])
        self.assertIn(item['id'], [row['id'] for row in self.inventory.items(location_id='bedroom')])
        deleted = self.inventory.delete_item(item['id'])
        with self.assertRaises(InventoryError):
            self.inventory.get_item(item['id'])
        self.inventory.restore_item(deleted)
        with self.assertRaises(InventoryError):
            self.inventory.restore_item(deleted)
        self.inventory.close()
        self.inventory = Inventory(self.path)
        self.assertEqual(self.inventory.get_item(item['id'])['name'], 'Trip cable')
        self.assertTrue(any('Restored Trip cable' in row['message'] for row in self.inventory.activity()))

    def test_invalid_inputs_leave_database_and_activity_unchanged(self):
        before, logs = self.inventory.stats(), self.inventory.activity()
        patches = [dict(name=''), dict(quantity='-1'), dict(quantity='1.5'), dict(quantity=''),
                   dict(quantity='1000000'), dict(low_stock='-1'), dict(category='Unknown'),
                   dict(location_id='missing'), dict(location_id=''), dict(value='NaN'),
                   dict(value='Infinity'), dict(value='-3'), dict(value='100000001'),
                   dict(tags='x'*31), dict(tags=','.join(f'tag{n}' for n in range(13))),
                   dict(notes='a'*2001), dict(photo=b'not an image')]
        for changes in patches:
            with self.subTest(changes=changes), self.assertRaises(InventoryError):
                self.inventory.save_item(self.new_item(**changes))
            self.assertEqual(self.inventory.stats(), before)
            self.assertEqual(self.inventory.activity(), logs)

    def test_zero_quantity_low_stock_and_quantity_limits(self):
        item = self.inventory.save_item(self.new_item(quantity='1'))
        self.inventory.adjust_quantity(item['id'], -1)
        self.assertEqual(self.inventory.get_item(item['id'])['quantity'], 0)
        self.assertIn(item['id'], [row['id'] for row in self.inventory.items(low_only=True)])
        with self.assertRaises(InventoryError):
            self.inventory.adjust_quantity(item['id'], -1)
        self.inventory.adjust_quantity(item['id'], 1)
        self.inventory.adjust_quantity(item['id'], 1)
        self.assertNotIn(item['id'], [row['id'] for row in self.inventory.items(low_only=True)])
        self.inventory.save_item(self.new_item(quantity='999999'), item['id'])
        with self.assertRaises(InventoryError):
            self.inventory.adjust_quantity(item['id'], 1)
        with self.assertRaises(InventoryError):
            self.inventory.adjust_quantity(item['id'], 2)

    def test_create_rename_and_reparent_whole_location_subtree(self):
        house = self.inventory.save_location(dict(name='Studio', kind='House'))
        room = self.inventory.save_location(dict(name='Workshop', kind='Room', parent_id=house['id']))
        storage = self.inventory.save_location(dict(name='Workbench', kind='Storage', parent_id=room['id']))
        drawer = self.inventory.save_location(dict(name='Bottom drawer', kind='Drawer', parent_id=storage['id']))
        item = self.inventory.save_item(self.new_item(location_id=drawer['id']))
        self.assertEqual(self.inventory.path_names(drawer['id']), ['Studio', 'Workshop', 'Workbench', 'Bottom drawer'])
        self.inventory.save_location(dict(name='Repair room', kind='Room', parent_id=house['id']), room['id'])
        self.assertEqual(self.inventory.items(query='repair room')[0]['id'], item['id'])
        self.inventory.save_location(dict(name='Workbench', kind='Storage', parent_id='garage'), storage['id'])
        self.assertEqual(self.inventory.path_names(drawer['id']), ['Home', 'Garage', 'Workbench', 'Bottom drawer'])
        self.assertNotIn(item['id'], [row['id'] for row in self.inventory.items(location_id=house['id'])])
        self.assertIn(item['id'], [row['id'] for row in self.inventory.items(location_id='garage')])

    def test_cycles_duplicates_and_missing_parents_are_rejected(self):
        before = self.inventory.locations()
        cases = [(dict(name='Home', kind='House', parent_id='cables'), 'home'),
                 (dict(name='Desk', kind='Storage', parent_id='desk'), 'desk'),
                 (dict(name='OFFICE', kind='Room', parent_id='home'), None),
                 (dict(name='home', kind='House'), None),
                 (dict(name='Nowhere', kind='Room', parent_id='missing'), None),
                 (dict(name='', kind='Room', parent_id='home'), None)]
        for data, location_id in cases:
            with self.subTest(data=data), self.assertRaises(InventoryError):
                self.inventory.save_location(data, location_id)
            self.assertEqual(before, self.inventory.locations())

    def test_delete_empty_location_after_moving_its_items(self):
        for location_id in ('home', 'office', 'cables'):
            with self.assertRaisesRegex(InventoryError, 'not empty'):
                self.inventory.delete_location(location_id)
        location = self.inventory.save_location(dict(name='Temporary box', kind='Container', parent_id='garage'))
        item = self.inventory.save_item(self.new_item(location_id=location['id']))
        with self.assertRaises(InventoryError):
            self.inventory.delete_location(location['id'])
        self.inventory.save_item(self.new_item(location_id='tools'), item['id'])
        self.inventory.delete_location(location['id'])
        self.assertNotIn(location['id'], [row['id'] for row in self.inventory.locations()])

    def test_filters_path_search_and_sorting(self):
        self.assertEqual(len(self.inventory.items(query='USB-C', tag='usb-c', category='Electronics', location_id='office')), 2)
        self.assertEqual(len(self.inventory.items(low_only=True)), 3)
        self.assertGreaterEqual(len(self.inventory.items(query='wardrobe')), 5)
        self.assertEqual(self.inventory.items(query='not a real item'), [])
        by_name = self.inventory.items(sort='Name A–Z')
        self.assertEqual([row['name'] for row in by_name], sorted((row['name'] for row in by_name), key=str.casefold))
        by_quantity = self.inventory.items(sort='Quantity: low first')
        self.assertEqual(by_quantity[-1]['quantity'], 36)
        by_value = self.inventory.items(sort='Value: high first')
        values = [row['quantity'] * row['value'] for row in by_value]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_photo_survives_edit_reopen_and_sqlite_backup(self):
        item = self.inventory.save_item(self.new_item(photo=PNG, photo_name='cable.png'))
        no_photo_argument = self.new_item(name='Renamed photo item')
        del no_photo_argument['photo']
        del no_photo_argument['photo_name']
        self.inventory.save_item(no_photo_argument, item['id'])
        self.assertEqual(self.inventory.get_item(item['id'])['photo'], PNG)
        target = Path(self.temp.name) / 'backup.sqlite3'
        self.inventory.backup(target)
        backup = Inventory(target)
        try:
            self.assertEqual(backup.get_item(item['id'])['photo'], PNG)
            self.assertEqual(backup.stats(), self.inventory.stats())
            self.assertEqual(backup.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            backup.close()
        with self.assertRaises(InventoryError):
            self.inventory.backup(self.path)

    def test_csv_exports_filtered_rows_with_paths_and_formula_text_escaped(self):
        item = self.inventory.save_item(self.new_item(name='=SUM(1,2)', notes='Unicode → notes'))
        target = Path(self.temp.name) / 'inventory.csv'
        self.inventory.export_csv(target, [item])
        with target.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "'=SUM(1,2)")
        self.assertEqual(rows[1][3], 'Home → Office → Oak desk → Cable drawer')
        self.assertEqual(rows[1][-1], 'Unicode → notes')

    def test_reset_is_complete_and_reseeds_data(self):
        self.inventory.save_item(self.new_item(photo=PNG, photo_name='test.png'))
        self.inventory.save_location(dict(name='Other home', kind='House'))
        self.inventory.reset_samples()
        self.assertEqual(self.inventory.stats()['items'], 18)
        self.assertEqual(self.inventory.stats()['locations'], 16)
        self.assertTrue(all(item['photo'] == b'' for item in self.inventory.items()))
        self.assertEqual(self.inventory.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(len(self.inventory.activity()), 2)

    def test_transaction_rolls_back_record_and_tags_if_logging_fails(self):
        before = self.inventory.stats()
        with patch.object(self.inventory, '_log', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.inventory.save_item(self.new_item())
        self.assertEqual(self.inventory.stats(), before)
        self.assertEqual(self.inventory.items(query='Braided spare'), [])
        self.assertEqual(self.inventory.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_foreign_keys_are_enforced_at_database_level(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.inventory.db.execute("DELETE FROM locations WHERE id='cables'")
        self.assertEqual(self.inventory.get_location('cables')['name'], 'Cable drawer')

    def test_missing_record_errors_are_clear(self):
        for fn in (self.inventory.get_item, self.inventory.delete_item, self.inventory.delete_location):
            with self.assertRaisesRegex(InventoryError, 'no longer exists'):
                fn('missing')

    def test_intentionally_empty_database_stays_empty_on_reopen(self):
        empty_path = Path(self.temp.name) / 'empty.sqlite3'
        other = Inventory(empty_path, sample=False)
        other.close()
        other = Inventory(empty_path)
        try:
            self.assertEqual(other.stats()['items'], 0)
            self.assertEqual(other.stats()['locations'], 0)
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
