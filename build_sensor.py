"""Package only our original telemetry scripts as a local L4D2 addon."""
import pathlib
import shutil
import vpk
ROOT=pathlib.Path(__file__).resolve().parent
from project_paths import GAME_DIR
GAME=GAME_DIR
stage=ROOT/'private'/'addon-build'
scripts=stage/'scripts'/'vscripts'
scripts.mkdir(parents=True,exist_ok=True)
for name in ('astra_sensor.nut','astra_bootstrap.nut','mapspawn_addon.nut','astra_runtime_start.nut','director_base_addon.nut'):
    shutil.copyfile(ROOT/'sensor'/name,scripts/name)
(stage/'addoninfo.txt').write_text('''"AddonInfo"
{
 "addonSteamAppID" "550"
 "addontitle" "Astra Jev observation sensor"
 "addonversion" "1"
 "addonauthor" "Astra"
 "addonDescription" "Local game-state observation. Does not change combat or campaign rules."
 "addonContent_Script" "1"
}
''',encoding='ascii')
target=GAME/'addons'/'astra_observer.vpk'
package=vpk.new(str(stage))
package.version=1  # L4D2 rejects the library's default VPK2 package.
package.save(str(target))
ems=GAME/'ems'/'astra'
ems.mkdir(parents=True,exist_ok=True)
shutil.copyfile(ROOT/'sensor'/'astra_sensor.nut',ems/'sensor-live.nut')
import hashlib
(ems/'sensor-version.txt').write_text(hashlib.sha256((ems/'sensor-live.nut').read_bytes()).hexdigest(),encoding='ascii')
print({'addon':target.name,'bytes':target.stat().st_size,'files':list(vpk.open(str(target)))})
