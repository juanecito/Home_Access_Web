"""Minimal ctypes binding to Linux-PAM: authenticate a password and check the account.

Only the privileged helper uses this: pam_unix needs root to verify other users' passwords.
"""
from ctypes import CDLL, CFUNCTYPE, POINTER, Structure, byref, c_char_p, c_int, c_size_t, c_void_p, cast, sizeof
from ctypes.util import find_library

PAM_SUCCESS = 0
PAM_BUF_ERR = 5
PAM_CONV_ERR = 19
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_DISALLOW_NULL_AUTHTOK = 0x0001


class PamMessage(Structure):
    _fields_ = [("msg_style", c_int), ("msg", c_char_p)]


class PamResponse(Structure):
    # resp must be malloc()ed: PAM frees it.
    _fields_ = [("resp", c_void_p), ("resp_retcode", c_int)]


ConvFunc = CFUNCTYPE(c_int, c_int, POINTER(POINTER(PamMessage)), POINTER(POINTER(PamResponse)), c_void_p)


class PamConv(Structure):
    _fields_ = [("conv", ConvFunc), ("appdata_ptr", c_void_p)]


_libc = CDLL(find_library("c"))
_libc.calloc.argtypes = [c_size_t, c_size_t]
_libc.calloc.restype = c_void_p
_libc.strdup.argtypes = [c_char_p]
_libc.strdup.restype = c_void_p

_libpam = CDLL(find_library("pam"))
_libpam.pam_start.argtypes = [c_char_p, c_char_p, POINTER(PamConv), POINTER(c_void_p)]
_libpam.pam_start.restype = c_int
_libpam.pam_end.argtypes = [c_void_p, c_int]
_libpam.pam_end.restype = c_int
for _name in ("pam_authenticate", "pam_acct_mgmt"):
    getattr(_libpam, _name).argtypes = [c_void_p, c_int]
    getattr(_libpam, _name).restype = c_int


def _run(service, username, password):
    """Run pam_authenticate (when a password is given) then pam_acct_mgmt. True on success."""
    secret = password.encode("utf-8") if password is not None else None
    if secret is not None and b"\0" in secret:
        return False

    def conversation(count, messages, responses, _appdata):
        if secret is None:
            return PAM_CONV_ERR
        addr = _libc.calloc(count, sizeof(PamResponse))
        if not addr:
            return PAM_BUF_ERR
        replies = cast(addr, POINTER(PamResponse))
        for i in range(count):
            if messages[i].contents.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                replies[i].resp = _libc.strdup(secret)
        responses[0] = replies
        return PAM_SUCCESS

    callback = ConvFunc(conversation)  # keep a reference for the lifetime of the transaction
    conv = PamConv(callback, None)
    handle = c_void_p()
    rc = _libpam.pam_start(service.encode(), username.encode(), byref(conv), byref(handle))
    if rc != PAM_SUCCESS:
        return False
    try:
        if secret is not None:
            rc = _libpam.pam_authenticate(handle, PAM_DISALLOW_NULL_AUTHTOK)
        if rc == PAM_SUCCESS:
            rc = _libpam.pam_acct_mgmt(handle, PAM_DISALLOW_NULL_AUTHTOK)
        return rc == PAM_SUCCESS
    finally:
        _libpam.pam_end(handle, rc)


def authenticate(service, username, password):
    return _run(service, username, password)


def account_ok(service, username):
    """Account still valid (not locked, not expired) — no password check."""
    return _run(service, username, None)
