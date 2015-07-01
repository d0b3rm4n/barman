# Copyright (C) 2013-2015 2ndQuadrant Italia (Devise.IT S.r.L.)
#
# This file is part of Barman.
#
# Barman is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Barman is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Barman.  If not, see <http://www.gnu.org/licenses/>.

import os
import shutil

import dateutil
from mock import Mock, patch
import pytest

from barman import xlog
from barman.infofile import WalFileInfo
from barman.recovery_executor import Assertion
from barman.command_wrappers import CommandFailedException
from barman.recovery_executor import RecoveryExecutor
import testing_helpers


# noinspection PyMethodMayBeStatic
class TestRecoveryExecutor(object):
    """
    this class tests the methods of the recovery_executor module
    """

    def test_rsync_backup_executor_init(self):
        """
        Test the construction of a RecoveryExecutor
        """

        # Test
        server = testing_helpers.build_mocked_server()
        backup_manager = Mock(server=server, config=server.config)
        assert RecoveryExecutor(backup_manager)

    def test_analyse_temporary_config_files(self, tmpdir):
        """
        Test the method that identifies dangerous options into the configuration
         files
        """
        # Build directory/files structure for testing
        tempdir = tmpdir.mkdir('tempdir')
        recovery_info = {
            'configuration_files': ['postgresql.conf', 'postgresql.auto.conf'],
            'tempdir': tempdir.strpath,
            'temporary_configuration_files': [],
            'results': {'changes': [], 'warnings': []}
        }
        postgresql_conf = tempdir.join('postgresql.conf')
        postgresql_auto = tempdir.join('postgresql.auto.conf')
        postgresql_conf.write('archive_command = something\n'
                              'data_directory = something')
        postgresql_auto.write('archive_command = something\n'
                              'data_directory = something')
        local_rec = tmpdir.mkdir('local_rec')
        recovery_info['temporary_configuration_files'].append(
            postgresql_conf.strpath)
        recovery_info['temporary_configuration_files'].append(
            postgresql_auto.strpath)
        # Build a RecoveryExecutor object (using a mock as server and backup
        # manager.
        server = testing_helpers.build_mocked_server()
        backup_manager = Mock(server=server, config=server.config)
        executor = RecoveryExecutor(backup_manager)
        # Identify dangerous options into config files for remote recovery
        executor.analyse_temporary_config_files(recovery_info)
        assert len(recovery_info['results']['changes']) == 2
        assert len(recovery_info['results']['warnings']) == 2
        # Prepare for a local recovery test
        recovery_info['results']['changes'] = []
        recovery_info['results']['warnings'] = []
        postgresql_conf_local = local_rec.join('postgresql.conf')
        postgresql_auto_local = local_rec.join('postgresql.auto.conf')
        postgresql_conf_local.write('archive_command = something\n'
                                    'data_directory = something')
        postgresql_auto_local.write('archive_command = something\n'
                                    'data_directory = something')
        # Identify dangerous options for local recovery
        executor.analyse_temporary_config_files(recovery_info)
        assert len(recovery_info['results']['changes']) == 2
        assert len(recovery_info['results']['warnings']) == 2

    def test_map_temporary_config_files(self, tmpdir):
        """
        Test the method that prepares configuration files for the final steps of
         a recovery
        """
        # Build directory/files structure for testing
        tempdir = tmpdir.mkdir('tempdir')
        recovery_info = {
            'configuration_files': ['postgresql.conf', 'postgresql.auto.conf'],
            'tempdir': tempdir.strpath,
            'temporary_configuration_files': [],
            'results': {'changes': [], 'warnings': []},
        }

        backup_info = testing_helpers.build_test_backup_info()
        backup_info.config.basebackups_directory = tmpdir.strpath
        datadir = tmpdir.mkdir(backup_info.backup_id).mkdir('data')
        postgresql_conf_local = datadir.join('postgresql.conf')
        postgresql_auto_local = datadir.join('postgresql.auto.conf')
        postgresql_conf_local.write('archive_command = something\n'
                                    'data_directory = something')
        postgresql_auto_local.write('archive_command = something\n'
                                    'data_directory = something')
        # Build a RecoveryExecutor object (using a mock as server and backup
        # manager.
        server = testing_helpers.build_mocked_server()
        backup_manager = Mock(server=server, config=server.config)
        executor = RecoveryExecutor(backup_manager)
        executor.map_temporary_config_files(recovery_info,
                                            backup_info, 'ssh@something')
        # check that configuration files have been moved by the method
        assert tempdir.join('postgresql.conf').check()
        assert tempdir.join('postgresql.conf').computehash() == \
            postgresql_conf_local.computehash()
        assert tempdir.join('postgresql.auto.conf').check()
        assert tempdir.join('postgresql.auto.conf').computehash() == \
            postgresql_auto_local.computehash()

    def test_setup(self):
        """
        Test the method that set up a recovery
        """
        backup_info = testing_helpers.build_test_backup_info()
        server = testing_helpers.build_mocked_server()
        backup_manager = Mock(server=server, config=server.config)
        executor = RecoveryExecutor(backup_manager)
        backup_info.version = 90300
        # no postgresql.auto.conf on version 9.3
        ret = executor.setup(backup_info, None, "/tmp")
        assert "postgresql.auto.conf" not in ret['configuration_files']
        # Check the present for postgresql.auto.conf on version 9.4
        backup_info.version = 90400
        ret = executor.setup(backup_info, None, "/tmp")
        assert "postgresql.auto.conf" in ret['configuration_files']
        # Receive a error if the remote command is invalid
        with pytest.raises(SystemExit):
            executor.setup(backup_info, "invalid", "/tmp")

    def test_set_pitr_targets(self, tmpdir):
        """
        Evaluate targets for point in time recovery
        """
        # Build basic folder/files structure
        tempdir = tmpdir.mkdir('temp_dir')
        dest = tmpdir.mkdir('dest')
        wal_dest = tmpdir.mkdir('wal_dest')
        recovery_info = {
            'configuration_files': ['postgresql.conf', 'postgresql.auto.conf'],
            'tempdir': tempdir.strpath,
            'results': {'changes': [], 'warnings': []},
            'is_pitr': False,
            'wal_dest': wal_dest.strpath,
        }
        backup_info = testing_helpers.build_test_backup_info()
        server = testing_helpers.build_mocked_server()
        backup_manager = Mock(server=server, config=server.config)
        # Build a recovery executor
        executor = RecoveryExecutor(backup_manager)
        executor.set_pitr_targets(recovery_info, backup_info,
                                  dest.strpath,
                                  '', '', '', '')
        # Test with empty values (no PITR)
        assert recovery_info['target_epoch'] is None
        assert recovery_info['target_datetime'] is None
        assert recovery_info['wal_dest'] == wal_dest.strpath
        # Test for PITR targets
        executor.set_pitr_targets(recovery_info, backup_info,
                                  dest.strpath,
                                  'target_name',
                                  '2015-06-03 16:11:03.71038+02',
                                  '2',
                                  None,)
        assert recovery_info['target_datetime'] == dateutil.parser.parse(
            '2015-06-03 16:11:03.710380+02:00')
        assert recovery_info['target_epoch'] == 1433344263.7103801
        assert recovery_info['wal_dest'] == dest.join('barman_xlog').strpath

    @patch('barman.recovery_executor.Rsync')
    def test_generate_recovery_conf(self, rsync_pg_mock, tmpdir):
        """
        Test the generation of recovery.conf file
        """
        # Build basic folder/files structure
        recovery_info = {
            'configuration_files': ['postgresql.conf', 'postgresql.auto.conf'],
            'tempdir': tmpdir.strpath,
            'results': {'changes': [], 'warnings': []},
        }
        backup_info = testing_helpers.build_test_backup_info()
        dest = tmpdir.mkdir('destination')
        # Build a recovery executor using a real server
        server = testing_helpers.build_real_server()
        executor = RecoveryExecutor(server.backup_manager)
        executor.generate_recovery_conf(recovery_info, backup_info,
                                        dest.strpath,
                                        True, 'remote@command',
                                        'target_name',
                                        '2015-06-03 16:11:03.71038+02', '2',
                                        '')
        # Check that the recovery.conf file exists
        recovery_conf_file = tmpdir.join("recovery.conf")
        assert recovery_conf_file.check()
        # Parse the generated recovery.conf
        recovery_conf = {}
        for line in recovery_conf_file.readlines():
            key, value = (s.strip() for s in line.strip().split('=', 1))
            recovery_conf[key] = value
        # check for contents
        assert 'recovery_end_command' in recovery_conf
        assert 'recovery_target_time' in recovery_conf
        assert 'recovery_target_timeline' in recovery_conf
        assert 'recovery_target_xid' not in recovery_conf
        assert 'recovery_target_name' in recovery_conf
        assert recovery_conf['recovery_end_command'] == "'rm -fr barman_xlog'"
        assert recovery_conf['recovery_target_time'] == \
            "'2015-06-03 16:11:03.71038+02'"
        assert recovery_conf['recovery_target_timeline'] == '2'
        assert recovery_conf['recovery_target_name'] == "'target_name'"

    @patch('barman.recovery_executor.RsyncPgData')
    def test_recover_basebackup_copy(self, rsync_pg_mock, tmpdir):
        """
        Test the copy of a content of a backup during a recovery
        :param rsync_pg_mock: Mock rsync object for the purpose if this test
        """
        # Build basic folder/files structure
        dest = tmpdir.mkdir('destination')
        server = testing_helpers.build_real_server()
        backup_info = testing_helpers.build_test_backup_info(
            server=server,
            tablespaces=[('tbs1', 16387, '/fake/location')])
        # Build a executor
        executor = RecoveryExecutor(server.backup_manager)
        executor.config.tablespace_bandwidth_limit = {'tbs1': ''}
        executor.config.bandwidth_limit = 10
        executor.basebackup_copy(
            backup_info, dest.strpath, tablespaces=None)
        rsync_pg_mock.assert_called_with(
            network_compression=False, bwlimit=10, ssh=None,
            exclude_and_protect=['/pg_tblspc/16387'])
        rsync_pg_mock.assert_any_call(
            network_compression=False, bwlimit='', ssh=None, check=True)
        rsync_pg_mock.smart_copy.assert_called_once(
            '/some/barman/home/main/base/1234567890/data/',
            dest.strpath, None)

    @patch('barman.recovery_executor.RsyncPgData')
    def test_recover_xlog(self, rsync_pg_mock, tmpdir):
        """
        Test the recovery of the xlogs of a backup
        :param rsync_pg_mock: Mock rsync object for the purpose if this test
        """
        # Build basic folders/files structure
        dest = tmpdir.mkdir('destination')
        wals = tmpdir.mkdir('wals')
        xlog_dir = wals.mkdir(xlog.hash_dir('000000000000000000000002'))
        xlog_file = xlog_dir.join('000000000000000000000002')
        xlog_file.write('dummy content')
        server = testing_helpers.build_real_server(
            main_conf={'wals_directory': wals.strpath})
        # build executor
        executor = RecoveryExecutor(server.backup_manager)
        required_wals = (WalFileInfo.from_xlogdb_line(
            '000000000000000000000002\t42\t43\tNone\n'),)
        executor.xlog_copy(required_wals, dest.strpath, None)
        # check for a correct invocation of rsync using local paths
        rsync_pg_mock.from_file_list.assert_called_once(
            ['000000000000000000000002'],
            xlog_dir.strpath,
            dest.strpath)
        # reset mock calls
        rsync_pg_mock.reset_mock()
        required_wals = (WalFileInfo.from_xlogdb_line(
            '000000000000000000000002\t42\t43\tNone\n'),)
        executor.backup_manager.compression_manager = Mock()
        executor.xlog_copy(required_wals, dest.strpath, 'remote_command')
        # check for the invocation of rsync on a remote call
        rsync_pg_mock.assert_called_once(network_compression=False,
                                         bwlimit=None,
                                         ssh='remote_command')

    def test_prepare_tablespaces(self, tmpdir):
        """
        Test tablespaces preparation for recovery
        """
        # Prepare basic directory/files structure
        dest = tmpdir.mkdir('destination')
        wals = tmpdir.mkdir('wals')
        backup_info = testing_helpers.build_test_backup_info(
            tablespaces=[('tbs1', 16387, '/fake/location')])
        # build an executor
        server = testing_helpers.build_real_server(
            main_conf={'wals_directory': wals.strpath})
        executor = RecoveryExecutor(server.backup_manager)
        # use a mock as cmd obj
        cmd_mock = Mock()
        executor.prepare_tablespaces(backup_info, cmd_mock, dest.strpath, {})
        cmd_mock.create_dir_if_not_exists.assert_called_once(
            dest.join('pg_tblspc').strpath)
        cmd_mock.delete_if_exists.assert_called_once(
            dest.join('pg_tblspc').join('16387').strpath)
        cmd_mock.create_symbolic_link.assert_called_once(
            '/fake/location',
            dest.join('pg_tblspc').join('16387').strpath)

    @patch('barman.recovery_executor.RsyncPgData')
    @patch('barman.recovery_executor.UnixRemoteCommand')
    def test_recovery(self, remote_cmd_mock, rsync_pg_mock, tmpdir):
        """
        Test the execution of a recovery
        """
        # Prepare basic directory/files structure
        dest = tmpdir.mkdir('destination')
        base = tmpdir.mkdir('base')
        wals = tmpdir.mkdir('wals')
        backup_info = testing_helpers.build_test_backup_info(tablespaces=[])
        backup_info.config.basebackups_directory = base.strpath
        backup_info.config.wals_directory = wals.strpath
        backup_info.version = 90400
        datadir = base.mkdir(backup_info.backup_id).mkdir('data')
        backup_info.pgdata = datadir.strpath
        postgresql_conf_local = datadir.join('postgresql.conf')
        postgresql_auto_local = datadir.join('postgresql.auto.conf')
        postgresql_conf_local.write('archive_command = something\n'
                                    'data_directory = something')
        postgresql_auto_local.write('archive_command = something\n'
                                    'data_directory = something')
        shutil.copy2(postgresql_conf_local.strpath, dest.strpath)
        shutil.copy2(postgresql_auto_local.strpath, dest.strpath)
        # Build an executor
        server = testing_helpers.build_real_server(
            global_conf={
                "barman_lock_directory": tmpdir.mkdir('lock').strpath
            },
            main_conf={
                "wals_directory": wals.strpath
            })
        executor = RecoveryExecutor(server.backup_manager)
        # test local recovery
        rec_info = executor.recover(backup_info, dest.strpath, None, None, None,
                                    None, None, True, None)
        # remove not usefull keys from the result
        del rec_info['cmd']
        sys_tempdir = rec_info['tempdir']
        assert rec_info == {
            'rsync': None,
            'tempdir': sys_tempdir,
            'wal_dest': dest.join('pg_xlog').strpath,
            'recovery_dest': 'local',
            'destination_path': dest.strpath,
            'temporary_configuration_files': [
                dest.join('postgresql.conf').strpath,
                dest.join('postgresql.auto.conf').strpath],
            'results': {
                'delete_barman_xlog': False,
                'changes': [
                    Assertion._make([
                        'postgresql.conf',
                        0,
                        'archive_command',
                        'false']),
                    Assertion._make([
                        'postgresql.auto.conf',
                        0,
                        'archive_command',
                        'false'])],
                'warnings': [
                    Assertion._make([
                        'postgresql.conf',
                        2,
                        'data_directory',
                        'something']),
                    Assertion._make([
                        'postgresql.auto.conf',
                        2,
                        'data_directory',
                        'something'])]},
            'target_epoch': None,
            'configuration_files': [
                'postgresql.conf',
                'postgresql.auto.conf'],
            'target_datetime': None,
            'safe_horizon': None,
            'is_pitr': False,
        }
        # test remote recovery
        rec_info = executor.recover(backup_info, dest.strpath, {}, None, None,
                                    None, None, True, "remote@command")
        # remove not usefull keys from the result
        del rec_info['cmd']
        del rec_info['rsync']
        sys_tempdir = rec_info['tempdir']
        assert rec_info == {
            'tempdir': sys_tempdir,
            'wal_dest': dest.join('pg_xlog').strpath,
            'recovery_dest': 'remote',
            'destination_path': dest.strpath,
            'temporary_configuration_files': [
                os.path.join(sys_tempdir, 'postgresql.conf'),
                os.path.join(sys_tempdir, 'postgresql.auto.conf')],
            'results': {
                'delete_barman_xlog': False,
                'changes': [
                    Assertion._make([
                        'postgresql.conf',
                        0,
                        'archive_command',
                        'false']),
                    Assertion._make([
                        'postgresql.auto.conf',
                        0,
                        'archive_command',
                        'false'])],
                'warnings': [
                    Assertion._make([
                        'postgresql.conf',
                        2,
                        'data_directory',
                        'something']),
                    Assertion._make([
                        'postgresql.auto.conf',
                        2,
                        'data_directory',
                        'something'])]},
            'target_epoch': None,
            'configuration_files': [
                'postgresql.conf',
                'postgresql.auto.conf'],
            'target_datetime': None,
            'safe_horizon': None,
            'is_pitr': False,
        }
        # test failed rsync
        rsync_pg_mock.side_effect = CommandFailedException()
        with pytest.raises(CommandFailedException):
            executor.recover(backup_info, dest.strpath, {}, None, None, None,
                             None, True, "remote@command")
