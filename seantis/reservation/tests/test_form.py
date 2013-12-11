from unittest2 import TestCase
from seantis.reservation.form import ReservationDataView


class TestForm(TestCase):

    def setUp(self):
        self.form = ReservationDataView()

    def test_none_is_represented_as_empty_string(self):
        self.assertEqual('', self.form.display_reservation_data(None))

    def test_true_is_represented_by_yes(self):
        self.assertEqual('Yes', self.form.display_reservation_data(True))

    def test_false_is_represented_by_no(self):
        self.assertEqual('No', self.form.display_reservation_data(False))
