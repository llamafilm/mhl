"""
__author__ = "Alexander Sahm"
__copyright__ = "Copyright 2020, Pomfort GmbH"

__license__ = "MIT"
__maintainer__ = "Patrick Renner, Alexander Sahm"
__email__ = "opensource@pomfort.com"
"""

import os
from freezegun import freeze_time
from click.testing import CliRunner

from mhl.history import MHLHistory
import mhl.commands

scenario_output_path = 'examples/scenarios/Output'
fake_ref_path = '/ref'


@freeze_time("2020-01-16 09:15:00")
def test_seal_succeed(fs):
    fs.create_file('/root/Stuff.txt', contents='stuff\n')
    fs.create_file('/root/A/A1.txt', contents='A1\n')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root'])
    assert not result.exception
    assert os.path.exists('/root/ascmhl/root_2020-01-16_091500_0001.mhl')
    # with open('/root/ascmhl/root_2020-01-16_091500_0001.mhl', 'r') as fin:
    #     print(fin.read())
    assert os.path.exists('/root/ascmhl/chain.txt')


@freeze_time("2020-01-16 09:15:00")
def test_seal_directory_hashes(fs):
    fs.create_file('/root/Stuff.txt', contents='stuff\n')
    fs.create_file('/root/A/A1.txt', contents='A1\n')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v'], catch_exceptions=False)
    assert result.exit_code == 0

    # a directory hash for the folder A was created
    hash_list = MHLHistory.load_from_path('/root').hash_lists[0]
    assert hash_list.find_media_hash_for_path('A').is_directory
    assert hash_list.find_media_hash_for_path('A').hash_entries[0].hash_string == 'ee2c3b94b6eecb8d'
    # and the directory hash of the root folder is set in the header
    assert hash_list.root_media_hash.hash_entries[0].hash_string == '15ef0ade91fff267'

    # add some more files and folders
    fs.create_file('/root/B/B1.txt', contents='B1\n')
    fs.create_file('/root/A/A2.txt', contents='A2\n')
    fs.create_file('/root/A/AA/AA1.txt', contents='AA1\n')
    os.mkdir('/root/emptyFolderA')
    os.mkdir('/root/emptyFolderB')
    os.mkdir('/root/emptyFolderC')
    os.mkdir('/root/emptyFolderC/emptyFolderCA')
    os.mkdir('/root/emptyFolderC/emptyFolderCB')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v'])
    assert result.exit_code == 0

    hash_list = MHLHistory.load_from_path('/root').hash_lists[-1]
    # due to the additional content the directory hash of folder A and the root folder changed
    assert hash_list.find_media_hash_for_path('A').hash_entries[0].hash_string == '47e7687ce4800633'
    assert hash_list.root_media_hash.hash_entries[0].hash_string == '5f4af3b3fd736415'
    # empty folder all have the same directory hash
    assert hash_list.find_media_hash_for_path('emptyFolderA').hash_entries[0].hash_string == 'ef46db3751d8e999'
    assert hash_list.find_media_hash_for_path('emptyFolderB').hash_entries[0].hash_string == 'ef46db3751d8e999'
    # but since we also contain the file names in the dir hashes an empty folder that contains other empty folders
    # has a different directory hash
    assert hash_list.find_media_hash_for_path('emptyFolderC').hash_entries[0].hash_string == '877071123901a4db'

    # altering the content of one file
    with open('/root/A/A2.txt', "a") as file:
        file.write('!!')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v'])
    assert 'hash mismatch for /root/A/A2.txt' in result.output
    hash_list = MHLHistory.load_from_path('/root').hash_lists[-1]
    # an altered file leads to a different root directory hash
    assert hash_list.root_media_hash.hash_entries[0].hash_string == 'adf18c910489663c'

    # rename one file
    os.rename('/root/B/B1.txt', '/root/B/B2.txt')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v'])
    assert 'hash mismatch for /root/A/A2.txt' in result.output
    # in addition to the failing verification we also have a missing file B1/B1.txt
    assert 'missing files:\n  /root/B/B1.txt' in result.output
    hash_list = MHLHistory.load_from_path('/root').hash_lists[-1]
    # the file name is part of the directory hash of the containing directory so it's hash changes
    assert hash_list.find_media_hash_for_path('B').hash_entries[0].hash_string == '8cdb106e71c4989d'
    # a renamed file also leads to a different root directory hash
    assert hash_list.root_media_hash.hash_entries[0].hash_string == '01441cdf1803e2b8'


@freeze_time("2020-01-16 09:15:00")
def test_seal_no_directory_hashes(fs):
    fs.create_file('/root/Stuff.txt', contents='stuff\n')
    fs.create_file('/root/A/A1.txt', contents='A1\n')
    os.mkdir('/root/emptyFolder')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v', '-d'])
    assert result.exit_code == 0

    # a directory entry without hash was created for the folder A
    hash_list = MHLHistory.load_from_path('/root').hash_lists[0]
    assert hash_list.find_media_hash_for_path('A').is_directory
    assert len(hash_list.find_media_hash_for_path('A').hash_entries) == 0
    # and no directory hash of the root folder is set in the header
    assert len(hash_list.root_media_hash.hash_entries) == 0
    # the empty folder is still referenced even if not creating directory hashes
    assert hash_list.find_media_hash_for_path('emptyFolder').is_directory

    # removing an empty folder will cause sealing to fail
    os.removedirs('/root/emptyFolder')
    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root', '-v', '-d'])
    assert result.exit_code == 15
    assert '1 missing files:\n  /root/emptyFolder' in result.output


def test_seal_fail_altered_file(fs, simple_mhl_history):
    # alter a file
    with open('/root/Stuff.txt', "a") as file:
        file.write('!!')

    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root'])
    assert result.exit_code == 12
    assert '/root/Stuff.txt' in result.output


def test_seal_fail_missing_file(fs, nested_mhl_histories):
    """
    test that sealing fails if there is a file missing on the file system that is referenced by one of the histories
    """

    root_history = MHLHistory.load_from_path('/root')
    paths = root_history.set_of_file_paths()

    assert paths == {'/root/B/B1.txt', '/root/B/BB/BB1.txt', '/root/Stuff.txt', '/root/A/AA/AA1.txt'}
    os.remove('/root/A/AA/AA1.txt')
    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root'])
    assert result.exit_code == 15
    assert '1 missing files:\n  /root/A/AA/AA1.txt' in result.output

    # the actual seal has been written to disk anyways we expect the history to contain
    # the new not yet referenced files (/root/B/BA/BA1.txt and /root/A/AB/AB1.txt) as well now
    root_history = MHLHistory.load_from_path('/root')
    paths = root_history.set_of_file_paths()

    # since we scan all generations for file paths we now get old files, missing files and new files here
    # as well as all entries for the directories
    assert paths == {'/root/B/B1.txt', '/root/B/BA/BA1.txt', '/root/B', '/root/A/AA', '/root/A/AB/AB1.txt',
                     '/root/B/BA', '/root/A/AA/AA1.txt', '/root/A/AB', '/root/Stuff.txt', '/root/B/BB',
                     '/root/A', '/root/B/BB/BB1.txt'}

    # since the file /root/A/AA/AA1.txt is still missing all further seal attempts will still fail
    runner = CliRunner()
    result = runner.invoke(mhl.commands.seal, ['/root'])
    assert result.exit_code == 15
    assert '1 missing files:\n  /root/A/AA/AA1.txt' in result.output
