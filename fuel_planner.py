"""Observed fuel inventory plus installed-map search locations, not game writes."""
import json
from navigation import distance,flat_distance

class FuelPlanner:
    def __init__(self,root,nav):
        self.path=root/'fuel-plan-state.json';self.nav=nav;self.goal=None
        self.data={'map':nav.map,'game_t':0,'cans':{},'searched':[],'processed_pickups':[]}
        if self.path.exists():
            saved=json.loads(self.path.read_text())
            if saved.get('map')==nav.map:self.data=saved
        source=root/'fuel-search-sites.json'
        self.sites=json.loads(source.read_text()).get(nav.map,[]) if source.exists() else []
        self.last_save=-100;self.started_t=None;self.dirty=False
        config=root/'fuel-staging-config.json'
        self.staging=json.loads(config.read_text()) if config.exists() else {}
    def save(self,t):
        self.data['game_t']=t
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,indent=2));tmp.replace(self.path)
        self.last_save=t;self.dirty=False
    def update(self,s,memory):
        p=s['player'];t=s['t']
        config=self.staging;landing_max_z=config.get('landing_max_z',180)
        if self.started_t is None:
            if self.data['game_t']>t or self.data.get('round_id')!=s.get('round_id'):
                self.data.update(cans={},searched=[],processed_pickups=[],staging_complete=False,released={},delivery=None)
                self.data['round_id']=s.get('round_id');self.goal=None;self.save(t)
            self.started_t=t
        cans=self.data['cans'];before=json.dumps(self.data,sort_keys=True)
        for item in s.get('items',[])+s.get('interactables',[]):
            if item['type']=='point_prop_use_target':self.data['delivery']=dict(item)
        released=self.data.setdefault('released',{})
        for key,record in getattr(memory,'released_fuel',{}).items():
            if record['t']>released.get(key,{}).get('t',-1):released[key]=dict(record)
        # A pickup is no longer a collection destination. A subsequently
        # observed dropped can will be added again at its actual position.
        for event in memory.outcomes:
            if event['status']=='verified' and event['task'].startswith('carry_'):
                token=f"{event['task']}:{event['game_t']}"
                if token not in self.data['processed_pickups']:
                    cans.pop(event['task'][6:],None)
                    self.data['processed_pickups'].append(token)
        visible={}
        held_ids={w.get('id') for w in p.get('weapons',{}).values() if w.get('type')=='weapon_gascan'}
        for item in s.get('items',[]):
            if item['type']!='weapon_gascan':continue
            key=str(item['id'])
            if item.get('owned') or item['id'] in held_ids:
                cans.pop(key,None)
                if key in released:released[key]['status']='reacquired'
            else:
                visible[key]=item
                throw=released.get(key)
                if throw and throw['status']=='released':
                    old=cans.get(key,{}).get('item')
                    stable=(old and distance(item['p'],old['p'])<3)
                    if not stable:throw.pop('stable_since',None)
                    else:
                        throw.setdefault('stable_since',t)
                        if t-throw['stable_since']>=.5:
                            throw.update(status='landed' if item['p'][2]<landing_max_z else 'misplaced',
                                landing_p=list(item['p']),confirmed_t=t)
                cans[key]={'item':dict(item),'seen_t':t}
        for ident in held_ids:
            if str(ident) in released:released[str(ident)]['status']='reacquired'
        for key,record in list(cans.items()):
            # Only discard a missing remembered object after reaching its
            # actual location. Being out of view elsewhere is not evidence.
            if key not in visible and distance(p['p'],record['item']['p'])<100 and t-record['seen_t']>3:
                # Nearby across a wall is not the item's actual location.
                # Retain it until we have reached its observed floor area.
                try:
                    reached=self.nav.closest_area(record['item']['p'],below=True,reachable_from=p['area'])==p['area']
                except RuntimeError:
                    reached=False
                if reached:cans.pop(key)
        for ident in getattr(memory,'consumed_fuel',()):cans.pop(str(ident),None)
        for site in self.sites:
            if site['id'] not in self.data['searched'] and flat_distance(p['p'],site['p'])<180 and abs(p['p'][2]-site['p'][2])<80:
                self.data['searched'].append(site['id'])
        flags=s.get('metrics',{}).get('flags',{})
        # Only a round-specific, visually reviewed balcony can enable throws.
        # Count actual observed lower-level cans, never inferred landings.
        # Live throws also landed on the reachable152unit stair landing.
        enabled=(config.get('enabled') is True and config.get('round_id')==s.get('round_id')
            and config.get('map')==s['map'] and config.get('reviewed_image')
            and config.get('observer')=='Astra visual review in current thread'
            and 1<=config.get('required_cans',0)<=13 and config.get('spots'))
        enabled=bool(enabled and s.get('metrics',{}).get('pours',0)>=config.get('min_pours_before_staging',0))
        misplaced={k:r for k,r in released.items() if r['status']=='misplaced'}
        memory.fuel_landing_problem=misplaced if enabled else None
        def throw_spots(point):
            return [spot for spot in config.get('spots',[]) if abs(spot['p'][2]-point[2])<80]
        pending={k:r for k,r in released.items() if r['status']=='released'}
        ground=sum(r['item']['p'][2]<landing_max_z and k not in pending for k,r in cans.items())
        if enabled and ground+s.get('metrics',{}).get('pours',0)>=config['required_cans']:
            self.data['staging_complete']=True
        # Enough releases justify searching below, not claiming landings or
        # pours. Reacquire each actual can before allowing its pickup.
        search_below=bool(enabled and ground+len(pending)+s.get('metrics',{}).get('pours',0)>=config['required_cans'])
        staging=bool(enabled and flags.get('finale_start') and not self.data.get('staging_complete') and not search_below)
        memory.fuel_stage_upper=staging
        memory.fuel_stage_floors=[spot['p'][2] for spot in config.get('spots',[])] if staging else []
        memory.staging_target=None
        memory.review_each_throw=bool(staging and config.get('review_each_throw',True))
        if staging and p['weapon']=='weapon_gascan' and p['p'][2]>180:
            nearby=[spot for spot in config['spots'] if abs(spot['p'][2]-p['p'][2])<80]
            if nearby:memory.staging_target=min(nearby,key=lambda spot:distance(p['p'],spot['p']))
        # A pickup can change memory within the save throttle, then disappear
        # from view. Keep that change dirty until saved even on quiet frames.
        if before!=json.dumps(self.data,sort_keys=True):self.dirty=True
        if self.dirty and t-self.last_save>=1:self.save(t)
        if memory.fuel_landing_problem:return None
        if not flags.get('finale_start') or p['weapon']=='weapon_gascan':return None
        known=[dict(r['item'],fuel_key=k,source='observed fuel') for k,r in cans.items()
               if k not in pending and memory.allowed('carry_'+k,t)
               and (not staging or (r['item']['p'][2]>180 and throw_spots(r['item']['p'])))]
        landing_search=[{'id':'landing_'+k,'p':r['search_p'],'type':'search',
            'source':'previously reviewed landing zone; this throw is unverified'} for k,r in pending.items()]
        ground_phase=bool(enabled and (self.data.get('staging_complete') or search_below))
        if ground_phase:
            below=[item for item in known if item['p'][2]<landing_max_z]
            # Lower cans can disappear or be consumed. Once none remain,
            # reacquire actual remembered upper fuel instead of permanently
            # ignoring it in favor of distant, unconfirmed search locations.
            if below or landing_search:known=below
        unexplored=[dict(site,source='search location',type='search') for site in self.sites
            if site['id'] not in self.data['searched']
            and (not staging or (site['p'][2]>180 and throw_spots(site['p'])))]
        candidates=known or (landing_search if ground_phase else []) or unexplored
        if not ground_phase:
            # Remembering one distant can must not hide much nearer places
            # we have not searched. Search goals still require reacquisition;
            # their uncertainty is priced below, never treated as a pickup.
            candidates=known+[site for site in unexplored if not any(
                flat_distance(site['p'],item['p'])<180 and abs(site['p'][2]-item['p'][2])<80
                for item in known)]
        if staging and not candidates:
            # Exhausted upper searches: use actual remembered ground cans.
            self.data['staging_complete']=True;memory.fuel_stage_upper=False;staging=False;self.save(t)
            candidates=[dict(r['item'],fuel_key=k,source='observed fuel') for k,r in cans.items() if k not in pending and memory.allowed('carry_'+k,t)] or landing_search
        # Retain a useful destination instead of oscillating as the player
        # rounds a corner. A pickup/empty search removes it from candidates.
        if self.goal:
            same=next((i for i in candidates if i.get('fuel_key',i['id'])==self.goal.get('fuel_key',self.goal['id'])),None)
            if same:self.goal=same;return same
        reachable=[];delivery_area=None
        delivery=self.data.get('delivery')
        if delivery:
            point=list(delivery['p']);point[2]-=max(40,min(64,p['eye'][2]-p['p'][2]))
            try:delivery_area=self.nav.closest_area(point,below=True,reachable_from=p['area'])
            except RuntimeError:pass
        for item in candidates:
            try:
                area=self.nav.closest_area(item['p'],below=True,reachable_from=p['area'])
                route=self.nav.route(p['area'],area)
                if staging:
                    returns=[]
                    for spot in throw_spots(item['p']):
                        try:
                            destination=self.nav.closest_area(spot['p'],below=True,reachable_from=area)
                            path=self.nav.route(area,destination)
                            returns.append((sum(distance(self.nav.areas[a]['p'],self.nav.areas[b]['p']) for a,b in zip(path,path[1:])),path))
                        except RuntimeError:continue
                    if not returns:continue
                    returning=min(returns,key=lambda pair:pair[0])[1]
                else:
                    returning=self.nav.route(area,delivery_area) if delivery_area is not None else []
            except RuntimeError:continue
            cost=sum(distance(self.nav.areas[a]['p'],self.nav.areas[b]['p']) for a,b in zip(route,route[1:]))
            cost+=sum(distance(self.nav.areas[a]['p'],self.nav.areas[b]['p']) for a,b in zip(returning,returning[1:]))
            if item.get('source')=='search location':cost+=400
            reachable.append((cost+distance(p['p'],item['p'])*.05,item))
        self.goal=min(reachable,key=lambda pair:pair[0])[1] if reachable else None
        return self.goal
