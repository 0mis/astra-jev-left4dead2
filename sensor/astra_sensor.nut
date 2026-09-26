// Read-only local game telemetry.
if(!("AstraMetrics" in getroottable())) {
    local metrics=FileToString("astra/metrics-live.nut");
    if(metrics==null)throw "Observation metrics module missing";
    compilestring(metrics,"astra_metrics")();
}
local previousObserver=("AstraSensor" in getroottable())?::AstraSensor:null;
local sameMap=previousObserver!=null && previousObserver.navMap==Director.GetMapName();
::AstraSensor <- {
    seq = 0, timer = null,
    round_id=sameMap && "round_id" in previousObserver ? previousObserver.round_id : Director.GetMapName()+"-"+Time().tostring(),
    navMap = FileToString("astra/nav-"+Director.GetMapName()+".json")==null ? "" : Director.GetMapName(),
    navAreas = null, navIndex=0, navChunks=0,
    events = sameMap?previousObserver.events:[], outcome = sameMap?previousObserver.outcome:"active",
    hazardPoll=0, nearbyNav=[],
    ladderMap="", ladders=[],
    function V(v) { return [v.x, v.y, v.z]; },
    function J(x) {
        local t = typeof x;
        if (x == null) return "null";
        if (t == "bool") return x ? "true" : "false";
        if (t == "integer") return x.tostring();
        if (t == "float") return format("%.4f", x);
        if (t == "string") {
            local out = "\"";
            foreach (ch in x) {
                if (ch == 34 || ch == 92) out += "\\" + ch.tochar();
                else if (ch >= 32 && ch < 127) out += ch.tochar();
                else out += "?";
            }
            return out + "\"";
        }
        local out = (t == "array") ? "[" : "{";
        local first = true;
        foreach (k, val in x) {
            if (!first) out += ",";
            first = false;
            if (t != "array") out += J(k.tostring()) + ":";
            out += J(val);
        }
        return out + ((t == "array") ? "]" : "}");
    },
    function Save(path, value) { StringToFile("astra/" + path, J(value)); },
    function IntProp(e,key,fallback=0){return NetProps.HasProp(e,key)?NetProps.GetPropInt(e,key):fallback;},
    function FloatProp(e,key,fallback=0.0){return NetProps.HasProp(e,key)?NetProps.GetPropFloat(e,key):fallback;},
    function WeaponInfo(item,p) {
        local ammo=IntProp(item,"m_iPrimaryAmmoType",-1),reserve=-1;
        if(ammo>=0 && ammo<32 && NetProps.HasProp(p,"m_iAmmo"))reserve=NetProps.GetPropIntArray(p,"m_iAmmo",ammo);
        return {id=item.GetEntityIndex(),type=item.GetClassname(),clip=item.Clip1(),max_clip=item.GetMaxClip1(),reserve=reserve,
            next_attack=FloatProp(item,"m_flNextPrimaryAttack"),reloading=IntProp(item,"m_bInReload")!=0 || IntProp(item,"m_reloadState")!=0};
    },
    function Event(kind) {
        events.append({kind=kind, t=Time(), map=Director.GetMapName()});
        if (events.len() > 32) events.remove(0);
        Save("events.json", events);
    },
    function OnGameEvent_round_start(p) { outcome="active"; Event("round_start"); },
    function OnGameEvent_player_spawn(p) {},
    function OnGameEvent_map_transition(p) { outcome="map_transition"; Event(outcome); },
    function OnGameEvent_finale_win(p) { outcome="finale_win"; Event(outcome); },
    function OnGameEvent_mission_lost(p) { outcome="mission_lost"; Event(outcome); },
    function OnGameEvent_finale_vehicle_ready(p) { Event("finale_vehicle_ready"); },
    function OnGameEvent_finale_start(p) { Event("finale_start"); },
    function OnGameEvent_gauntlet_finale_start(p) { Event("gauntlet_finale_start"); },
    function NavExport() {
        local name = Director.GetMapName();
        if (name == navMap) return;
        if(navAreas==null) {
            local all={}; NavMesh.GetAllAreas(all); navAreas=[];
            foreach(id,a in all) navAreas.append(a);
        }
        local rows=[],limit=navIndex+32;
        for(;navIndex<navAreas.len() && navIndex<limit;navIndex++) {
            local a=navAreas[navIndex];
            local adjacent = [];
            for (local dir=0; dir<4; dir++) {
                local list={}; a.GetAdjacentAreas(dir,list);
                foreach (ignored, b in list) adjacent.append(b.GetID());
            }
            local center=a.GetCenter();
            rows.append({id=a.GetID(),p=V(center),nw=V(a.GetCorner(0)),se=V(a.GetCorner(2)),
                         flow=GetFlowDistanceForPosition(center),adj=adjacent,
                         attr=a.GetAttributes(),spawn=a.GetSpawnAttributes()});
        }
        if(rows.len()>0) {Save("nav-"+name+"-"+navChunks+".json",{areas=rows}); navChunks++;}
        if(navIndex>=navAreas.len()) {
            Save("nav-"+name+".json",{map=name,chunks=navChunks,area_count=navAreas.len()}); navMap=name;
        }
    },
    function Visible(p, e, pos) {
        local tr={start=p.EyePosition(),end=pos,ignore=p,mask=33579137};
        TraceLine(tr);
        return ("fraction" in tr && tr.fraction > 0.97) || ("enthit" in tr && tr.enthit == e);
    },
    function ReadLadders() {
        local name=Director.GetMapName();
        if(ladderMap==name) return;
        local all={},areas={};NavMesh.GetAllLadders(all);NavMesh.GetAllAreas(areas);ladders=[];
        foreach(id,l in all) {
            if(!l.IsValid() || !l.IsUsableByTeam(2)) continue;
            local bottom=l.GetBottomArea(),top=l.GetTopArea();
            local tops=[];foreach(ignored,a in areas)if(l.IsConnected(a,0))tops.append(a.GetID());
            ladders.append({id=l.GetID(),bottom=V(l.GetBottomOrigin()),top=V(l.GetTopOrigin()),
                bottom_area=bottom==null?null:bottom.GetID(),top_area=top==null?null:top.GetID(),
                top_areas=tops,dir=l.GetDir(),width=l.GetWidth()});
        }
        Save("ladders-"+name+".json",{map=name,ladders=ladders});ladderMap=name;
    },
    function Tick() {
        try {
            local p=GetListenServerHost();
            if (p == null || !p.IsValid()) return 0.1;
            ReadLadders();
            local pos=p.GetOrigin(), eye=p.EyePosition(), ang=p.EyeAngles();
            local dominator=p.GetSpecialInfectedDominatingMe();
            local area=NavMesh.GetNavArea(pos,120.0), weapon=p.GetActiveWeapon();
            local nearest=false;
            if(area==null) {area=NavMesh.GetNearestNavArea(pos,160.0,true,true);nearest=true;}
            if(outcome=="mission_lost" && !p.IsDead() && !p.IsDying() && !p.IsIncapacitated()
                && p.GetHealth()>0 && NetProps.GetPropInt(p,"m_currentReviveCount")==0
                && GetCurrentFlowDistanceForPlayer(p)<1000
                && area!=null && (area.GetSpawnAttributes() & 2048)!=0) {
                ::AstraMetrics.Reset();events=[];outcome="active";
                round_id=Director.GetMapName()+"-"+Time().tostring();
                Event("observed_respawn_after_loss");
            }
            local inv={}, inventory={},weapons={}; GetInvTable(p,inv);
            foreach (slot, item in inv) if(item!=null) {inventory[slot] <- item.GetClassname();weapons[slot] <- WeaponInfo(item,p);}
            local state={version=1,seq=++seq,t=Time(),map=Director.GetMapName(),round_id=round_id,
                mode=Director.GetGameMode(),outcome=outcome,events=events,
                player={id=p.GetEntityIndex(),p=V(pos),eye=V(eye),angles=[ang.x,ang.y,ang.z],
                        velocity=V(p.GetVelocity()),health=p.GetHealth(),temp_health=p.GetHealthBuffer(),
                        dead=p.IsDead()||p.IsDying(),incap=p.IsIncapacitated(),
                        pinned=p.IsDominatedBySpecialInfected(),ledge=p.IsHangingFromLedge(),
                        dominator_type=dominator==null?0:dominator.GetZombieType(),
                        immobilized=p.IsImmobilized(),buttons=p.GetButtonMask(),
                        on_ladder=NetProps.HasProp(p,"movetype")?NetProps.GetPropInt(p,"movetype")==9:false,
                        staggering=p.IsStaggering(),water_level=IntProp(p,"m_nWaterLevel"),
                        revive_count=IntProp(p,"m_currentReviveCount"),weapons=weapons,
                        next_attack=FloatProp(p,"m_flNextAttack"),
                        on_fire=p.IsOnFire(),area_damaging=area==null?false:area.IsDamaging(),
                        area=area==null?null:area.GetID(),nearest_area=nearest,flow=GetCurrentFlowDistanceForPlayer(p),
                        weapon=weapon==null?null:weapon.GetClassname(),
                        clip=weapon==null?0:weapon.Clip1(),inventory=inventory},
                enemies=[],teammates=[],items=[],interactables=[],obstacles=[],metrics=::AstraMetrics.Snapshot()};
            local e=null;
            while ((e=Entities.FindByClassname(e,"player"))!=null) {
                if(e==p) continue;
                if(e.IsSurvivor()) {
                    local gear={}; GetInvTable(e,gear);
                    local kit="slot3" in gear && gear.slot3!=null && gear.slot3.GetClassname()=="weapon_first_aid_kit";
                    state.teammates.append({id=e.GetEntityIndex(),p=V(e.GetOrigin()),
                        health=e.GetHealth(),has_medkit=kit,bot=IsPlayerABot(e),dead=e.IsDead()||e.IsDying(),
                        incap=e.IsIncapacitated(),pinned=e.IsDominatedBySpecialInfected(),ledge=e.IsHangingFromLedge()});
                } else if(!e.IsDead()&&!e.IsDying()&&!e.IsGhost()&&(e.GetOrigin()-pos).Length()<1800) {
                    local aim=e.GetOrigin()+Vector(0,0,45);
                    if(Visible(p,e,aim)) state.enemies.append({id=e.GetEntityIndex(),type=e.GetZombieType(),
                        p=V(aim),velocity=V(e.GetVelocity()),health=e.GetHealth(),incap=e.IsIncapacitated()});
                }
            }
            e=null;
            while((e=Entities.FindByClassnameWithin(e,"witch",pos,1800))!=null) {
                local aim=e.GetOrigin()+Vector(0,0,42);
                if(e.GetHealth()>0 && Visible(p,e,aim))state.enemies.append({id=e.GetEntityIndex(),type=7,
                    p=V(aim),velocity=V(e.GetVelocity()),health=e.GetHealth(),rage=FloatProp(e,"m_rage")});
            }
            e=null; local candidates=[];
            while ((e=Entities.FindByClassnameWithin(e,"infected",pos,1400))!=null) {
                if(e.GetHealth()>0) candidates.append(e);
            }
            candidates.sort(function(a,b){return ((a.GetOrigin()-pos).LengthSqr()-(b.GetOrigin()-pos).LengthSqr()).tointeger();});
            foreach (i, ent in candidates) {
                if(i>=24) break;
                local aim=ent.GetOrigin()+Vector(0,0,42);
                if(Visible(p,ent,aim)) state.enemies.append({id=ent.GetEntityIndex(),type=0,p=V(aim),
                    velocity=V(ent.GetVelocity()),health=ent.GetHealth(),model=ent.GetModelName(),forward=V(ent.GetForwardVector())});
            }
            e=null;
            while ((e=Entities.FindInSphere(e,pos,1800))!=null) {
                local cls=e.GetClassname();
                // Distant fuel/ammo still require a real unobstructed sightline.
                if((e.GetCenter()-pos).Length()>600 && cls!="weapon_gascan" && cls!="weapon_ammo_spawn") continue;
                if(cls.find("weapon_")==0 || cls.find("prop_door")==0 || cls=="func_button" || cls=="point_prop_use_target" ||
                   ((cls=="prop_physics" || cls=="prop_physics_multiplayer") && (e.GetModelName().find("cola")!=null || e.GetModelName().find("gascan")!=null))) {
                    if(state.items.len()<50 && (Visible(p,e,e.GetCenter()) || (cls.find("weapon_")==0 && Visible(p,e,e.GetCenter()+Vector(0,0,8))))) {
                        local item={id=e.GetEntityIndex(),type=cls,p=V(e.GetCenter()),model=e.GetModelName(),name=e.GetName(),owned=e.GetOwnerEntity()!=null};
                        if(cls.find("prop_door")==0 && NetProps.HasProp(e,"m_eDoorState"))
                            item.door_state <- NetProps.GetPropInt(e,"m_eDoorState");
                        state.items.append(item);
                    }
                }
            }
            for(local degree=-150;degree<=180;degree+=30) {
                local rad=(ang.y+degree)*0.01745329252;
                local start=pos+Vector(0,0,30),end=start+Vector(cos(rad),sin(rad),0)*160;
                local tr={start=start,end=end,ignore=p,mask=33636363}; TraceLine(tr);
                local obstacle={angle=degree,fraction=("fraction" in tr)?tr.fraction:0};
                if("enthit" in tr && tr.enthit!=null) {
                    local hit=tr.enthit,cls=hit.GetClassname();
                    obstacle.hit_type <- cls;obstacle.hit_id <- hit.GetEntityIndex();
                    if(cls!="player") {obstacle.hit_name <- hit.GetName();obstacle.hit_model <- hit.GetModelName();}
                }
                state.obstacles.append(obstacle);
            }
            foreach(cls in ["func_button","func_button_timed","func_elevator","func_door","prop_elevator","prop_door_rotating","prop_door_rotating_checkpoint","point_prop_use_target","point_script_use_target","trigger_finale","trigger_multiple"]) {
                e=null;
                while((e=Entities.FindByClassname(e,cls))!=null) {
                    local rescue=cls=="trigger_multiple";
                    if(rescue && (!((Director.GetMapName()=="c2m5_concert" && (e.GetName()=="stadium_exit_leftt_escape_trigger" || e.GetName()=="stadium_exit_right_escape_trigger")) || (Director.GetMapName()=="c3m4_plantation" && e.GetName()=="escape_boat_trigger") || (Director.GetMapName()=="c4m5_milltown_escape" && e.GetName()=="trigger_boat") || (Director.GetMapName()=="c5m5_bridge" && e.GetName()=="trigger_heli")) || !NetProps.HasProp(e,"m_bDisabled"))) continue;
                    local center=e.GetCenter();
                    if(rescue || (center-pos).Length()<900) {
                        local item={id=e.GetEntityIndex(),type=cls,name=e.GetName(),p=V(center),visible=Visible(p,e,center),
                            locked=IntProp(e,"m_bLocked")!=0,disabled=IntProp(e,"m_bDisabled")!=0,
                            spawnflags=IntProp(e,"m_spawnflags"),hammerid=IntProp(e,"m_iHammerID",-1)};
                        if(NetProps.HasProp(e,"m_eDoorState")) item.door_state <- NetProps.GetPropInt(e,"m_eDoorState");
                        if(cls=="func_elevator") item.velocity<-V(e.GetVelocity());
                        state.interactables.append(item);
                    }
                }
            }
            if(Time()>=hazardPoll) {
                local near={};NavMesh.GetNavAreasInRadius(pos,1000.0,near);nearbyNav=[];
                foreach(id,a in near) nearbyNav.append({id=a.GetID(),flow=GetFlowDistanceForPosition(a.GetCenter()),damaging=a.IsDamaging(),blocked=a.IsBlocked(2,false)});
                hazardPoll=Time()+0.5;
            }
            state.nearby_nav <- nearbyNav;
            state.ladders <- [];
            foreach(l in ladders) if((Vector(l.bottom[0],l.bottom[1],l.bottom[2])-pos).Length()<1200 || (Vector(l.top[0],l.top[1],l.top[2])-pos).Length()<1200) state.ladders.append(l);
            state.exit_checkpoint_occupied <- Director.IsAnySurvivorInExitCheckpoint();
            // Boolean only: never store or send the user's account name or ID.
            state.privacy <- {voice_disabled=Convars.GetFloat("voice_modenable")==0,
                              sv_cheats=Convars.GetFloat("sv_cheats"),
                              private_lan=Convars.GetFloat("sv_lan")==1};
            // Read-only gameplay audit; never set difficulty or enemy health.
            state.game_settings <- {difficulty=null,mode=null,tank_base_health=Convars.GetFloat("z_tank_health")};
            try {state.game_settings.difficulty=Convars.GetStr("z_difficulty");state.game_settings.mode=Convars.GetStr("mp_gamemode");} catch(ignored) {}
            state.nav_ready <- navMap==Director.GetMapName();
            Save("state.json",state);
            NavExport();
        } catch(error) { Save("sensor-error.json",{t=Time(),error=error.tostring()}); }
        return 0.1;
    }
};
printl("ASTRA_READONLY_SENSOR_LOADED");
