"""
This file defines the 'netconifyCmdo' class.
Used by the 'netconify' shell utility.
"""
import traceback
from lxml import etree
import sys
import warnings

from jnpr.junos.transport.tty_telnet import Telnet
from jnpr.junos.transport.tty_serial import Serial
from jnpr.junos.rpcmeta import _RpcMetaExec
from jnpr.junos.facts import *
import logging

QFX_MODEL_LIST = ['QFX3500', 'QFX3600', 'VIRTUAL CHASSIS']
QFX_MODE_NODE = 'NODE'
QFX_MODE_SWITCH = 'SWITCH'

logger = logging.getLogger("jnpr.junos.console")


class Console(object):

    def __init__(self, **kvargs):
        """
        NoobDevice object constructor.

        :param str host:
            **REQUIRED** host-name or ipaddress of target device

        :param str user:
            *OPTIONAL* login user-name, uses root if not provided

        :param str passwd:
            *OPTIONAL* in console connection for device at zeroized state
            password is not required

        :param int port:
            *OPTIONAL*  port, defaults to '23' for telnet mode and
            '/dev/ttyUSB0' for serial.

        :param int baud:
            *OPTIONAL*  baud, default baud rate is 9600

        :param str mode:
            *OPTIONAL*  mode, mode of connection (telnet/serial)
            default is telnet

        :param int timeout:
            *OPTIONAL*  timeout, default is 0.5

        :param int attempts:
            *OPTIONAL*  attempts, default is 10

        :param str ssh_config:
            *OPTIONAL* The path to the SSH configuration file.
            This can be used to load SSH information from a configuration file.
            By default ~/.ssh/config is queried it will be used by SCP class.
            So its assumed ssh is enabled by the time we use SCP functionality.

        :param bool gather_facts:
            *OPTIONAL* default is ``False``.  If ``False`` then the
            facts are not gathered on call to :meth:`open`

        """

        # ----------------------------------------
        # setup instance connection/open variables
        # ----------------------------------------

        self._tty = None
        self._facts = {}
        self.connected = False
        self._skip_logout = False
        self.results = dict(changed=False, failed=False, errmsg=None)

        # hostname is not required in serial mode connection
        self._hostname = kvargs.get('host')
        self._auth_user = kvargs.get('user', 'root')
        self._auth_password = kvargs.get(
            'password',
            '') or kvargs.get(
            'passwd',
            '')
        self._port = kvargs.get('port', '23')
        self._baud = kvargs.get('baud', '9600')
        self._mode = kvargs.get('mode', 'telnet')
        self._timeout = kvargs.get('timeout', '0.5')
        # self.timeout needed by PyEZ utils
        self.timeout = self._timeout
        self._attempts = kvargs.get('attempts', 10)
        self.gather_facts = kvargs.get('gather_facts', False)
        self.rpc = _RpcMetaExec(self)
        from jnpr.junos import Device
        self._ssh_config = kvargs.get('ssh_config')
        if sys.version < '3':
            self.cli = lambda cmd, format='text', warning=True: \
                Device.cli.im_func(self, cmd, format, warning)
            self.facts_refresh = lambda exception_on_failure=False: \
                Device.facts_refresh.im_func(self, exception_on_failure)
        else:
            self.cli = lambda cmd, format='text', warning=True: \
                Device.cli(self, cmd, format, warning)
            self.facts_refresh = lambda exception_on_failure=False: \
                Device.facts_refresh(self, exception_on_failure)
        self._ssh_config = kvargs.get('ssh_config')

    @property
    def _sshconf_path(self):
        from jnpr.junos import Device
        if sys.version < '3':
            ssh_conf_fn = lambda: Device._sshconf_lkup.im_func(self)
        else:
            ssh_conf_fn = lambda: Device._sshconf_lkup(self)
        return ssh_conf_fn()

    # ------------------------------------------------------------------------
    # property: hostname
    # ------------------------------------------------------------------------

    @property
    def hostname(self):
        """
        :returns: the host-name of the Junos device.
        """
        return self._hostname

    # ------------------------------------------------------------------------
    # property: user
    # ------------------------------------------------------------------------

    @property
    def user(self):
        """
        :returns: the login user (str) accessing the Junos device
        """
        return self._auth_user

    # ------------------------------------------------------------------------
    # property: password
    # ------------------------------------------------------------------------

    @property
    def password(self):
        """
        :returns: ``None`` - do not provide the password
        """
        return None  # read-only

    @password.setter
    def password(self, value):
        """
        Change the authentication password value.  This is handy in case
        the calling program needs to attempt different passwords.
        """
        self._auth_password = value

    # ------------------------------------------------------------------------
    # property: port
    # ------------------------------------------------------------------------

    @property
    def port(self):
        """
        :returns: the port (str) to connect to the Junos device
        """
        return self._port

    # ------------------------------------------------------------------------
    # property: facts
    # ------------------------------------------------------------------------

    @property
    def facts(self):
        """
        :returns: Device fact dictionary
        """
        return self._facts

    @facts.setter
    def facts(self, value):
        """ read-only property """
        raise RuntimeError("facts is read-only!")

    def open(self):
        """
        open the connection to the device
        """

        # ---------------------------------------------------------------
        # validate device hostname or IP address
        # ---------------------------------------------------------------

        if self._mode.upper() is 'TELNET' and self._hostname is None:
            self.results['failed'] = True
            self.results[
                'errmsg'] = 'ERROR: Device hostname/IP not specified !!!'
            return self.results

        # --------------------
        # login to the CONSOLE
        # --------------------
        try:
            self._tty_login()
        except RuntimeError as err:
            logger.error("ERROR:  {0}:{1}\n".format('login', str(err)))
            logger.error(
                "\nComplete traceback message: {0}".format(
                    traceback.format_exc()))
            raise err
        except Exception as ex:
            logger.error("Exception occurred: {0}:{1}\n".format('login', str(ex)))
            raise ex
        self.connected = True
        if self.gather_facts is True:
            logger.info('facts: retrieving device facts...')
            self.facts_refresh()
            self.results['facts'] = self._facts
        return self

    def close(self, skip_logout=False):
        """
        Closes the connection to the device.
        """
        if skip_logout is False and self.connected is True:
            try:
                self._tty_logout()
            except Exception as err:
                logger.error("ERROR {0}:{1}\n".format('logout', str(err)))
                raise err
            self.connected = False
        elif self.connected is True:
            try:
                self._tty._tty_close()
            except Exception as err:
                logger.error("ERROR {0}:{1}\n".format('close', str(err)))
                logger.error(
                    "\nComplete traceback message: {0}".format(
                        traceback.format_exc()))
                raise err
            self.connected = False

    # execute rpc calls
    def execute(self, rpc_cmd, *args, **kwargs):
        return self._tty.nc.rpc(etree.tounicode(rpc_cmd))

    # -------------------------------------------------------------------------
    # LOGIN/LOGOUT
    # -------------------------------------------------------------------------

    def _tty_login(self):
        tty_args = {}
        tty_args['user'] = self._auth_user
        tty_args['passwd'] = self._auth_password
        tty_args['timeout'] = float(self._timeout)
        tty_args['attempts'] = int(self._attempts)
        if self._mode.upper() == 'TELNET':
            tty_args['host'] = self._hostname
            tty_args['port'] = self._port
            self.console = ('telnet', self._hostname, self.port)
            self._tty = Telnet(**tty_args)
        elif self._mode.upper() == 'SERIAL':
            tty_args['port'] = self._port
            tty_args['baud'] = self._baud
            self.console = ('serial', self._port)
            self._tty = Serial(**tty_args)
        else:
            logger.error('Mode should be either telnet or serial')
            raise AttributeError('Mode to be telnet/serial')

        self._tty.login()

    def _tty_logout(self):
        self._tty.logout()

    def zeroize(self):
        """ perform device ZEROIZE actions """
        logger.info("zeroize : ZEROIZE device, rebooting")
        self._tty.nc.zeroize()
        self._skip_logout = True
        self.results['changed'] = True

    # -----------------------------------------------------------------------
    # Context Manager
    # -----------------------------------------------------------------------

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.connected:
            self.close()
