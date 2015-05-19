#!/usr/bin/python

from collectors import collect_lp_bugs
import time


while True:
    print "Getting remote sources..."
    collect_lp_bugs.collect()
    print "--------"
    time.sleep(5 * 60)
