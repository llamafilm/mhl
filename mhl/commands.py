"""
__author__ = "Patrick Renner, Alexander Sahm"
__copyright__ = "Copyright 2020, Pomfort GmbH"

__license__ = "MIT"
__maintainer__ = "Patrick Renner, Alexander Sahm"
__email__ = "opensource@pomfort.com"
"""


import datetime
import os
import platform
import click

from .hashlist import MHLCreatorInfo, MHLTool, MHLProcess, MHLMediaHash
from .hasher import create_filehash, DirectoryHashContext
from .history import MHLHistory
from . import logger
from .generator import MHLGenerationCreationSession
from .traverse import post_order_lexicographic
from .__version__ import ascmhl_supported_hashformats
from . import utils
import binascii
from lxml import etree


@click.command()
@click.argument('root_path', type=click.Path(exists=True))
@click.option('--verbose', '-v', default=False, is_flag=True, help="Verbose output")
@click.option('--directory_hashes', '-d', default=False, is_flag=True, help="Create directory hashes")
@click.option('--hash_format', '-h', type=click.Choice(ascmhl_supported_hashformats), multiple=False,
              default='xxh64', help="Algorithm")
def seal(root_path, verbose, hash_format, directory_hashes):
    """
    Creates a new generation from the content of a folder hierarchy.
    All files are hashed and will be compared to previous records in the `asc-mhl` folder if they exists.
    """
    logger.verbose_logging = verbose

    if not os.path.isabs(root_path):
        root_path = os.path.join(os.getcwd(), root_path)

    logger.verbose(f'seal folder at path: {root_path}')

    existing_history = MHLHistory.load_from_path(root_path)

    # we collect all paths we expect to find first and remove every path that we actually found while
    # traversing the file system, so this set will at the end contain the file paths not found in the file system
    not_found_paths = existing_history.set_of_file_paths()

    # start a verification session on the existing history
    session = MHLGenerationCreationSession(existing_history)

    num_failed_verifications = 0
    # store the directory hashes of sub folders so we can use it when calculating the hash of the parent folder
    dir_hash_mappings = {}
    for folder_path, children in post_order_lexicographic(root_path):
        # generate directory hashes
        dir_hash_context = None
        if directory_hashes:
            dir_hash_context = DirectoryHashContext(hash_format)
        for item_name, is_dir in children:
            file_path = os.path.join(folder_path, item_name)
            if is_dir:
                if not dir_hash_context:
                    continue
                hash_string = dir_hash_mappings.pop(file_path)
            else:
                hash_string, success = seal_file_path(existing_history, file_path, hash_format, session)
                if not success:
                    num_failed_verifications += 1
                not_found_paths.discard(file_path)
            if dir_hash_context:
                dir_hash_context.append_hash(hash_string, item_name)
        if dir_hash_context:
            dir_hash = dir_hash_context.final_hash_str()
            dir_hash_mappings[folder_path] = dir_hash
            logger.verbose(f'dir hash of {folder_path}: {dir_hash}')

    commit_session(session)

    if num_failed_verifications > 0:
        raise logger.VerificationFailedException()

    test_for_missing_files(not_found_paths)


@click.command()
@click.argument('root_path', type=click.Path(exists=True))
@click.option('--verbose', '-v', default=False, is_flag=True, help="Verbose output")
def check(root_path, verbose):
    """
    Checks existing generations against the file system.
    Traverses through the content of a folder, hashes all found files and compares ("verifies") the hashes
    against the records in the asc-mhl folder.
    """
    logger.verbose_logging = verbose

    if not os.path.isabs(root_path):
        root_path = os.path.join(os.getcwd(), root_path)

    logger.verbose(f'check folder at path: {root_path}')

    existing_history = MHLHistory.load_from_path(root_path)

    if len(existing_history.hash_lists) == 0:
        raise logger.NoMHLHistoryException(root_path)

    # we collect all paths we expect to find first and remove every path that we actually found while
    # traversing the file system, so this set will at the end contain the file paths not found in the file system
    not_found_paths = existing_history.set_of_file_paths()

    num_failed_verifications = 0
    num_new_files = 0
    for folder_path, children in post_order_lexicographic(root_path):
        for item_name, is_dir in children:
            file_path = os.path.join(folder_path, item_name)
            if is_dir:
                continue

            relative_path = existing_history.get_relative_file_path(file_path)
            history, history_relative_path = existing_history.find_history_for_path(relative_path)

            # check if there is an existing hash in the other generations and verify
            original_hash_entry = history.find_original_hash_entry_for_path(history_relative_path)

            # in case there is no original hash entry continue
            if original_hash_entry is None:
                logger.error(f'found new file {file_path}')
                num_new_files += 1
                continue

            # create a new hash and compare it against the original hash entry
            current_hash = create_filehash(original_hash_entry.hash_format, file_path)
            if original_hash_entry.hash_string == current_hash:
                logger.verbose(f'verification of file {file_path}: OK')
            else:
                logger.error(f'hash mismatch for {file_path} '
                             f'old {original_hash_entry.hash_format}: {original_hash_entry.hash_string}, '
                             f'new {original_hash_entry.hash_format}: {current_hash}')
                num_failed_verifications += 1

            not_found_paths.discard(file_path)

    if num_failed_verifications > 0:
        raise logger.VerificationFailedException()

    test_for_missing_files(not_found_paths)

    if num_new_files > 0:
        raise logger.NewFilesFoundException()


@click.command()
@click.argument('root_path', type=click.Path(exists=True))
@click.argument('paths', type=click.Path(exists=True), nargs=-1)
@click.option('--verbose', '-v', default=False, is_flag=True, help="enable verbose output")
@click.option('--hash_format', '-h', type=click.Choice(ascmhl_supported_hashformats), multiple=False,
              default='xxh64', help="hash algorithm to use")
def record(root_path, paths, verbose, hash_format):
    """
    Creates a new generation from the file or folder paths specified.

    \b
    ROOT_PATH: the root path to use for the asc mhl history
    PATHS: the file or folder paths to add to the asc mhl history

    This can be used for instance when adding single files to an already mhl-managed file hierarchy.
    All files that are specified or inside a specified folder are hashed and will be compared
    to previous records in the `asc-mhl` folder if they are recorded in the history already.
    The following files will not be handled by this command:

    \b
    * files that are referenced in the existing ascmhl history but not specified as input
    * files that are neither referenced in the history nor specified as input
    """
    logger.verbose_logging = verbose

    if not os.path.isabs(root_path):
        root_path = os.path.join(os.getcwd(), root_path)

    if len(paths) == 0:
        raise click.BadParameter('no file paths gives', param_hint='PATHS')

    existing_history = MHLHistory.load_from_path(root_path)
    # start a creation session on the existing history
    session = MHLGenerationCreationSession(existing_history)

    num_failed_verifications = 0
    for path in paths:
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        if os.path.isdir(path):
            for folder_path, children in post_order_lexicographic(path):
                for item_name, is_dir in children:
                    file_path = os.path.join(folder_path, item_name)
                    if is_dir:
                        continue
                    _, success = seal_file_path(existing_history, file_path, hash_format, session)
                    if not success:
                        num_failed_verifications += 1
        else:
            _, success = seal_file_path(existing_history, path, hash_format, session)
            if not success:
                num_failed_verifications += 1

    commit_session(session)

    if num_failed_verifications > 0:
        raise logger.VerificationFailedException()


@click.command()
@click.argument('file_path', type=click.Path(exists=True))
def validate(file_path):
    """Validates a mhl file against the xsd schema definition"""

    xsd_path = 'xsd/ASCMHL.xsd'
    xsd = etree.XMLSchema(etree.parse(xsd_path))

    # pass a file handle to support the fake file system used in the tests
    file = open(file_path, 'rb')
    result = xsd.validate(etree.parse(file))

    if result:
        logger.info(f'validated: {file_path}')
    else:
        logger.error(f'ERROR: {file_path} didn\'t validate against XSD!')
        logger.info(f'Issues:\n{xsd.error_log}')
        raise logger.VerificationFailedException


def test_for_missing_files(not_found_paths):
    if len(not_found_paths) > 0:
        logger.error(f"{len(not_found_paths)} missing files: ")
        for path in not_found_paths:
            logger.error(f"  {path}")
        raise logger.CompletenessCheckFailedException()


def commit_session(session):
    root_media_hash = MHLMediaHash()
    root_media_hash.path = session.root_history.get_root_path()
    creator_info = MHLCreatorInfo()
    creator_info.root_media_hash = root_media_hash
    creator_info.tool = MHLTool('seal', '0.0.1')
    creator_info.creation_date = utils.datetime_now_isostring()
    creator_info.host_name = platform.node()
    creator_info.process = MHLProcess('in-place')
    session.commit(creator_info)


def seal_file_path(existing_history, file_path, hash_format, session) -> (str, bool):
    relative_path = existing_history.get_relative_file_path(file_path)
    file_size = os.path.getsize(file_path)
    file_modification_date = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
    existing_hash_formats = existing_history.find_existing_hash_formats_for_path(relative_path)
    # in case there is no hash in the required format to use yet, we need to verify also against
    # one of the existing hash formats, we for simplicity use always the first hash format in this example
    # but one could also use a different one if desired
    success = True
    if len(existing_hash_formats) > 0 and hash_format not in existing_hash_formats:
        existing_hash_format = existing_hash_formats[0]
        hash_in_existing_format = create_filehash(existing_hash_format, file_path)
        # FIXME: test what happens if the existing hash verification fails in other format fails
        # should we then really create two entries
        success &= session.append_file_hash(file_path, file_size, file_modification_date,
                                            existing_hash_format, hash_in_existing_format)
    current_format_hash = create_filehash(hash_format, file_path)
    success &= session.append_file_hash(file_path, file_size, file_modification_date, hash_format, current_format_hash)
    return current_format_hash, success
