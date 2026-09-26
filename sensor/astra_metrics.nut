// Observation only. Store event facts, never player names or account IDs.
::AstraMetrics <- {
    version=1, shots=0, empty=0, heals=0, revives=0, pours=0, uses=0,
    last_use=null, hints=[], recent=[], flags={},
    function Reset() {
        shots=0;empty=0;heals=0;revives=0;pours=0;uses=0;
        last_use=null;hints=[];recent=[];flags={};
    },
    function Own(p) {
        return "userid" in p && GetPlayerFromUserID(p.userid)==GetListenServerHost();
    },
    function Note(kind,p) {
        local row={kind=kind,t=Time()};
        if(kind.find("finale")==0)flags[kind] <- true;
        if("subject" in p) row.subject <- p.subject;
        if("targetid" in p) row.target <- p.targetid;
        recent.append(row);if(recent.len()>24)recent.remove(0);
        if(kind.find("explain_")==0 || kind.find("gunshop_")==0 || kind.find("instruct")==0) {
            hints.append(row);if(hints.len()>12)hints.remove(0);
        }
    },
    function OnGameEvent_weapon_fire(p){if(Own(p))shots++;},
    function OnGameEvent_weapon_fire_on_empty(p){if(Own(p))empty++;},
    function OnGameEvent_heal_success(p){if(Own(p)){heals++;Note("heal_success",p);}},
    function OnGameEvent_revive_success(p){if(Own(p)){revives++;Note("revive_success",p);}},
    function OnGameEvent_gascan_pour_completed(p){if(Own(p)){pours++;Note("gascan_pour_completed",p);}},
    function OnGameEvent_player_use(p){if(Own(p)){uses++;last_use <- {target=p.targetid,t=Time()};}},
    function OnGameEvent_friendly_fire(p){
        if("attacker" in p && GetPlayerFromUserID(p.attacker)==GetListenServerHost())Note("friendly_fire",{});
    },
    function Handler(kind){return function(p){::AstraMetrics.Note(kind,p);};},
    function Snapshot(){return {shots=shots,empty=empty,heals=heals,revives=revives,pours=pours,uses=uses,last_use=last_use,recent=recent,hints=hints,flags=flags};}
};
foreach(kind in ["finale_start","finale_radio_start","finale_escape_start","finale_vehicle_ready","finale_win",
    "gauntlet_finale_start","explain_deactivate_alarm","explain_mall_alarm","explain_store_alarm",
    "explain_stage_finale_start","explain_c1m4_finale","c1m4_scavenge_instructions",
    "gascan_pour_blocked","gascan_pour_interrupted","triggered_car_alarm",
    "explain_gun_shop_tanker","explain_gun_shop","explain_store_item","explain_store_item_stop",
    "explain_return_item","explain_coaster","explain_coaster_stop","explain_ferry_button",
    "explain_bridge","explain_gates_are_open","explain_radio","explain_vehicle_arrival"]) {
    ::AstraMetrics["OnGameEvent_"+kind] <- ::AstraMetrics.Handler(kind);
}
__CollectGameEventCallbacks(::AstraMetrics);
