#!/usr/bin/env python3
"""Render the small set of Asterisk settings owned by this container."""

import os
import re
import socket
import sys
from ipaddress import IPv4Address
from pathlib import Path


CONFIG = Path("/etc/asterisk")


def setting(name: str, *, required: bool = False, pattern: str = r"[A-Za-z0-9._~:/-]+") -> str:
    value = os.environ.get(name, "").strip()
    if required and (not value or value == "CHANGE_ME"):
        raise ValueError(f"Set {name} in .env (scripts/setup.ps1 can generate it)")
    if value and not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid characters in {name}")
    return value


def write(name: str, contents: str) -> None:
    path = CONFIG / name
    path.write_text(contents.lstrip(), encoding="utf-8")
    path.chmod(0o640)


try:
    ami = setting("AMI_SECRET", required=True)
    ari = setting("ARI_SECRET", required=True)
    sip = setting("SIP_TEST_SECRET", required=True)
    operator = setting("SIP_OPERATOR_SECRET", required=True)
    public_ip = setting("ASTERISK_PUBLIC_IP", pattern=r"[A-Za-z0-9.:-]+")
    local_net = setting("ASTERISK_LOCAL_NET", pattern=r"(?:auto|[0-9./:a-fA-F]+)") or "auto"
    local_peer = setting("LOCAL_SIP_PEER_IP", pattern=r"[0-9.]+")
except ValueError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)

if local_net == "auto":
    addresses = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
    container_ip = next((item[4][0] for item in addresses if not IPv4Address(item[4][0]).is_loopback), None)
    if not container_ip:
        print("Could not detect container IPv4 address for ASTERISK_LOCAL_NET=auto", file=sys.stderr)
        sys.exit(1)
    local_net = f"{container_ip}/32"

nat = ""
if public_ip:
    nat += f"external_signaling_address={public_ip}\nexternal_media_address={public_ip}\n"
if local_net:
    nat += f"local_net={local_net}\n"

local_peer_config = ""
if local_peer:
    import ipaddress

    try:
        ipaddress.IPv4Address(local_peer)
    except ipaddress.AddressValueError as exc:
        print(f"Invalid LOCAL_SIP_PEER_IP: {exc}", file=sys.stderr)
        sys.exit(1)
    local_peer_config = f"""
[local-sip-peer]
type=endpoint
transport=transport-udp
context=from-inbound
disallow=all
allow=ulaw,alaw
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
identify_by=ip

[local-sip-peer-identify]
type=identify
endpoint=local-sip-peer
match={local_peer}
"""

write("manager.conf", f"""
[general]
enabled=yes
webenabled=no
port=5038
bindaddr=0.0.0.0
displayconnects=no

[voice-ai]
secret={ami}
read=system,call,log,verbose,agent,user,dialplan
write=call,originate,agent,user
""")

write("http.conf", """
[general]
enabled=yes
bindaddr=0.0.0.0
bindport=8088
""")

write("ari.conf", f"""
[general]
enabled=yes
pretty=no

[voice-ai]
type=user
read_only=no
password={ari}
""")

write("rtp.conf", """
[general]
rtpstart=10000
rtpend=10100
""")

write("pjsip.conf", f"""
[global]
type=global
user_agent=VoiceAI-Asterisk

[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
{nat}
[test-inbound]
type=endpoint
transport=transport-udp
context=from-inbound
disallow=all
allow=ulaw,alaw
auth=test-inbound-auth
aors=test-inbound
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[test-inbound-auth]
type=auth
auth_type=userpass
username=test-inbound
password={sip}

[test-inbound]
type=aor
max_contacts=1
remove_existing=yes

[1001]
type=endpoint
transport=transport-udp
context=from-inbound
disallow=all
allow=ulaw,alaw
auth=1001-auth
aors=1001
callerid="Demo client" <+77010000007>
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[1001-auth]
type=auth
auth_type=userpass
username=1001
password={sip}

[1001]
type=aor
max_contacts=1
remove_existing=yes

[operator]
type=endpoint
transport=transport-udp
context=from-inbound
disallow=all
allow=ulaw,alaw
auth=operator-auth
aors=operator
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[operator-auth]
type=auth
auth_type=userpass
username=operator
password={operator}

[operator]
type=aor
max_contacts=1
remove_existing=yes

{local_peer_config}
#include pjsip_trunk.conf
""")

write("extensions.conf", """
[general]
static=yes
writeprotect=yes

[from-inbound]
exten => s,1,NoOp(Incoming voice AI call)
 same => n,Answer()
 same => n,Stasis(voice-ai)
 same => n,Hangup()

; 7575NN represents demo client CNN (+770100000NN).
exten => _7575XX,1,Set(CALLERID(num)=+770100000${EXTEN:4:2})
 same => n,Answer()
 same => n,Stasis(voice-ai)
 same => n,Hangup()

; Direct SIP/RTP diagnostic: call 9999 and listen for your own voice.
exten => 9999,1,Answer()
 same => n,Echo()
 same => n,Hangup()

exten => _X.,1,Answer()
 same => n,Stasis(voice-ai)
 same => n,Hangup()
exten => _X,1,Goto(s,1)
exten => voice-ai,1,Goto(s,1)

[saqta-transfer]
exten => _[a-z].,1,NoOp(Transfer to ${EXTEN}: ${SAQTA_SUMMARY})
 same => n,Set(CALLERID(name)=Saqta ${EXTEN})
 same => n,Dial(PJSIP/operator,30)
 same => n,Hangup()
""")

write("modules.conf", """
[modules]
autoload=yes
noload=chan_sip.so
noload=chan_alsa.so
noload=chan_console.so
noload=res_pjsip_transport_websocket.so
noload=res_config_pgsql.so
noload=res_config_ldap.so
noload=res_geolocation.so
noload=res_pjsip_geolocation.so
noload=res_phoneprov.so
noload=res_pjsip_phoneprov_provider.so
noload=res_musiconhold.so
noload=cel_pgsql.so
noload=res_adsi.so
noload=app_adsiprog.so
noload=app_getcpeid.so
noload=app_macro.so
noload=cdr_radius.so
noload=cdr_sqlite3_custom.so
noload=cel_sqlite3_custom.so
noload=cdr_pgsql.so
noload=cdr_tds.so
noload=cel_tds.so
noload=cel_radius.so
noload=app_voicemail_imap.so
noload=app_voicemail_odbc.so
noload=chan_unistim.so
noload=pbx_dundi.so
noload=res_hep_rtcp.so
noload=res_hep_pjsip.so
""")

# Runtime sockets and log files must be writable after dropping privileges.
os.makedirs("/run/asterisk", exist_ok=True)
import pwd
import grp

uid = pwd.getpwnam("asterisk").pw_uid
gid = grp.getgrnam("asterisk").gr_gid
for path in [CONFIG / name for name in ("manager.conf", "http.conf", "ari.conf", "rtp.conf", "pjsip.conf", "extensions.conf", "modules.conf")]:
    os.chown(path, uid, gid)
os.chown("/run/asterisk", uid, gid)

os.execvp("asterisk", ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk", "-vvv"])
