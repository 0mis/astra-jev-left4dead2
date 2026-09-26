// Only observation startup: no campaign, combat, or Director changes.
if (!("AstraBootstrap" in getroottable())) IncludeScript("astra_bootstrap", getroottable());
::AstraBootstrap.Load();
__CollectGameEventCallbacks(::AstraBootstrap);
if ("AstraMetrics" in getroottable()) __CollectGameEventCallbacks(::AstraMetrics);
::AstraBootstrap.Start();
StringToFile("astra/startup.txt", Director.GetMapName()+" "+Time().tostring());
printl("ASTRA_OBSERVER_LATE_START");
