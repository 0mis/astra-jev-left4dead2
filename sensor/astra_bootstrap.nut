// Local observation-code hot reload avoids restarting the game for sensor fixes.
// The fixed path contains only our authored observation module, no commands.
::AstraBootstrap <- {
    timer=null, version="", lastTime=-1.0,
    function Load() {
        local v=FileToString("astra/sensor-version.txt");
        if(v!=null && v!=version) {
            local source=FileToString("astra/sensor-live.nut");
            if(source==null) throw "Observation module absent or exceeds 16 KiB";
            compilestring(source,"astra_local_observer")();
            version=v;
        }
    },
    function Tick() {
        try {
            Load();
            if(lastTime>Time()) ResetRound();
            lastTime=Time();
            if("AstraSensor" in getroottable()) return ::AstraSensor.Tick();
        }
        catch(error) { StringToFile("astra/bootstrap-error.txt",error.tostring()); }
        return 0.2;
    },
    function Start() {
        if(timer!=null && timer.IsValid()) return;
        timer=SpawnEntityFromTable("logic_script",{targetname="astra_observation_timer"});
        timer.ValidateScriptScope();
        timer.GetScriptScope().AstraObserve <- function(){return ::AstraBootstrap.Tick();};
        AddThinkToEnt(timer,"AstraObserve");
    },
    function Forward(kind,p) {
        try {
            Load(); Start();
            if("AstraSensor" in getroottable() && kind in ::AstraSensor) ::AstraSensor[kind](p);
        } catch(error) { StringToFile("astra/bootstrap-error.txt",error.tostring()); }
    },
    function ResetRound() {
        if("AstraMetrics" in getroottable()) ::AstraMetrics.Reset();
        if("AstraSensor" in getroottable()) {
            ::AstraSensor.outcome="active";::AstraSensor.events=[];::AstraSensor.seq=0;
            ::AstraSensor.round_id=Director.GetMapName()+"-"+Time().tostring();
        }
    },
    function OnGameEvent_round_start(p){Load();ResetRound();Forward("OnGameEvent_round_start",p);},
    function OnGameEvent_player_spawn(p){Forward("OnGameEvent_player_spawn",p);},
    function OnGameEvent_map_transition(p){Forward("OnGameEvent_map_transition",p);},
    function OnGameEvent_finale_win(p){Forward("OnGameEvent_finale_win",p);},
    function OnGameEvent_mission_lost(p){Forward("OnGameEvent_mission_lost",p);},
    function OnGameEvent_finale_vehicle_ready(p){Forward("OnGameEvent_finale_vehicle_ready",p);},
    function OnGameEvent_finale_start(p){Forward("OnGameEvent_finale_start",p);},
    function OnGameEvent_gauntlet_finale_start(p){Forward("OnGameEvent_gauntlet_finale_start",p);}
};
__CollectGameEventCallbacks(::AstraBootstrap);
printl("ASTRA_OBSERVER_BOOTSTRAP_READY");
