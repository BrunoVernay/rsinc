#!/usr/bin/python3

'''
merge

Copyright (c) 2019 C. J. Williams

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

drive_dir = '/home/conor/drive/'  # where config and data files will be stored

import argparse
import json
import os.path
import re
import subprocess
import sys
import time

from clint.textui import colored
from datetime import datetime

sys.path.insert(0, drive_dir)
os.chdir(drive_dir)
import spinner
from config import *

print('''/*
 * Copyright 2019 C. J. Williams (CHURCHILL COLLEGE)
 * This is free software with ABSOLUTELY NO WARRANTY
 */''')


LINE_FMT = re.compile(u'\s*([0-9]+) ([\d\-]+) ([\d:]+).([\d]+) (.*)')
TIME_FMT = '%Y-%m-%d %H:%M:%S'

strtobool = {'yes': True, 'ye': True, 'y': True, 'n': False, 'no': False,
             1: 'yes', 0: 'no', 't': True, 'true': True, 'f': False,
             'false': False, 'Y': True, 'N': False, 'Yes': True, "No": False,
             '': True}

counter = 0
total_jobs = 0

ylw = colored.yellow  # delete
cyn = colored.cyan  # push
mgt = colored.magenta  # pull
red = colored.red  # error/conflict

grn = colored.green  # normal info

swap = str.maketrans("/", '_')


class data():
    def __init__(self, base, arg, last):
        self.path = base + arg + '/'
        self.p_old = arg.translate(swap) + last + '.json'  # remove / from arg
        self.p_tmp = arg.translate(swap) + last + '.tmp.json'  # ^

        self.d_old = {}
        self.d_tmp = {}
        self.d_dif = {}

        self.s_old = set({})
        self.s_tmp = set({})
        self.s_dif = set({})

        self.s_low = set({})

    def build_dif(self):
        self.s_old = set(self.d_old)
        self.s_tmp = set(self.d_tmp)
        self.s_low = set(k.lower() for k in self.s_tmp)

        deleted = self.s_old.difference(self.s_tmp)
        created = self.s_tmp.difference(self.s_old)

        inter = self.s_tmp.intersection(self.s_old)

        for key in created:
            self.d_dif.update({key: 3})

        for key in deleted:
            self.d_dif.update({key: 2})

        for key in inter:
            if self.d_old[key]['bytesize'] != self.d_tmp[key]['bytesize']:
                self.d_dif.update({key: 1})
            elif self.d_tmp[key]['datetime'] > self.d_old[key]['datetime']:
                self.d_dif.update({key: 1})
            else:
                self.d_dif.update({key: 0})

        self.s_dif = set(self.d_dif)


class direct():
    def __init__(self, arg):
        self.lcl = data(base_l, arg, '_lcl')
        self.rmt = data(base_r, arg, '_rmt')
        self.path = arg

    def build_dif(self):
        self.lcl.build_dif()
        self.rmt.build_dif()


def log(*args):
    if verbosity:
        print(*args)


def read(file):
    '''
    Reads json do dict and returns dict
    '''
    log('Reading', file)
    with open(file, 'r') as fp:
        d = json.load(fp)

    return d


def write(file, d):
    '''
    Writes dict to json
    '''
    if dry_run:
        return
    else:
        log('Writing', file)
        with open(file, 'w') as fp:
            json.dump(d, fp, sort_keys=True, indent=4)


def lsl(path):
    '''
    Runs rclone lsl on path and returns a dict containing each file with the
    size and last modified time as integers
    '''
    command = ['rclone', 'lsl', path]
    result = subprocess.Popen(
        command, stdout=subprocess.PIPE, universal_newlines=True)

    d = {}

    for line in iter(result.stdout.readline, ''):
        g = LINE_FMT.match(line)

        size = int(g.group(1))
        age = g.group(2) + ' ' + g.group(3)
        date_time = int(time.mktime(
            datetime.strptime(age, TIME_FMT).timetuple()))

        filename = g.group(5)

        d[filename] = {u'bytesize': size, u'datetime': date_time}

    return d


def check_exist(path):
    if os.path.exists(path):
        log('Checked', path)
        return 0
    else:
        return 1


def empty():
    return {'fold': {}, 'file': {}}


def insert(main, chain):
    if len(chain) == 2:
        main['file'].update({chain[0]: chain[1]})
    else:
        main['fold'].update({chain[0]: empty()})
        insert(main['fold'][chain[0]], chain[1:])


def pack(d):
    nest = empty()

    for chain in [k.split('/') + [v] for k, v in d.items()]:
        insert(nest, chain)

    return nest


def unpack(nest, d={}, path=''):
    for k, v in nest['file'].items():
        d.update({path + k: v})

    for k, v in nest['fold'].items():
        d.update(unpack(v, d, k + '/'))

    return d


def _get_branch(nest, chain):
    if len(chain) == 0:
        return nest
    else:
        return _get_branch(nest['fold'][chain[0]], chain[1:])


def get_branch(nest, path):
    return _get_branch(nest, path.split('/'))


def _merge(nest, new, chain):
    if len(chain) == 1:
        nest['fold'][chain[0]].update(new)
    else:
        _merge(nest['fold'][chain[0]], new, chain[1:])


def merge(nest, path, new):
    _merge(nest, new, path.split('/'))


def _test(nest, chain):
    if len(chain) == 1:
        if chain[0] in nest['fold']:
            return 1
        else:
            return 0  # chain depth correct dir missing
    else:
        if chain[0] in nest['fold']:
            return _test(nest['fold'][chain[0]], chain[1:])
        else:
            return -1  # chain depth incorrect, too deep


def test(master, path):
    return _test(master, path.split('/'))


def _get_min(nest, chain, min_chain):
    if len(chain) == 1:
        return min_chain
    elif chain[0] not in nest['fold']:
        return min_chain
    else:
        min_chain.append(chain[1])
        return _get_min(nest['fold'][chain[0]], chain[1:], min_chain)


def get_min(master, path):
    chain = path.split('/')
    min_chain = _get_min(master, chain, [chain[0]])
    return '/'.join(min_chain)


d = lsl(base_l + 'test')

dry_run = False
verbosity = 1

nest = pack(d)

i = 'dir1/nest1'

r = test(nest, i)

if r == 1:
    print('have', i, 'can sync')
elif r == 0:
    print('dont have', i, 'must first run then merge')
else:
    print('must sinc', get_min(nest, i))

# print([k for k, v in nest['file'].items()])

# print([k for k, v in nest['fold'].items()])


folder = os.getcwd()
