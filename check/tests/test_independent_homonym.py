import unittest
from test_homonyms import pair, homonyms
from gwt_check import selftest

class IndependentHomonymTests(unittest.TestCase):
    def test_ten_calendar_years_is_not_more_than_ten(self):
        for a,b in [(2000,2010),(1996,2006),(1800,1810)]:
            with self.subTest(a=a,b=b):
                self.assertFalse(homonyms(pair(selftest._date(a,1,1),selftest._date(b,1,1))))
    def test_ten_years_plus_one_day_does_warn(self):
        self.assertTrue(homonyms(pair(selftest._date(2000,1,1),selftest._date(2010,1,2))))
    def test_open_intervals_overlap(self):
        self.assertFalse(homonyms(pair(selftest._date(1720,modifier=1),selftest._date(1700,modifier=2))))
    def test_year_zero_is_unknown(self):
        self.assertFalse(homonyms(pair(selftest._date(),selftest._date(1800))))
    def test_duplicate_event_reference_does_not_create_extra_people(self):
        data=pair();p=selftest._get(data,'people','I9');p['event_ref_list'].append(p['event_ref_list'][0].copy())
        self.assertEqual(len(homonyms(data)),1)
    def test_both_directions_have_the_same_interval_decision(self):
        dates=[(selftest._date(1700),selftest._date(1720,modifier=1)),
               (selftest._date(1700),selftest._date(1720,modifier=2)),
               (selftest._date(1700,modifier=3),selftest._date(1720))]
        for a,b in dates:self.assertEqual(bool(homonyms(pair(a,b))),bool(homonyms(pair(b,a))))
