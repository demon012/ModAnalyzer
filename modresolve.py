#!/usr/bin/python

import os
import sys
import pprint
import re
import glob

import modanalyzer
import modlist
import mcmodfixes

CHECK_CONFLICT_KINDS = ("block", "item", "biome", "recipes/smelting", "recipes/crafting/shapeless", "recipes/crafting/shaped")  # check for conflicts on these
RESOLVE_CONFLICT_KINDS = ("block", "item", "biome")

WANTED_MODS_FILENAME = "include.txt"

ID_RANGES = {
    "block": range(500, 4096),  # >256 for future vanilla block expansion, >408 for future itemblocks -- maximum, 12-bit
    "blocktg": range(1, 256),   # terrain generation blocks
    "item": range(5000, 32000),
    "biome": range(0, 256),
    }

"""Get an available block ID."""
def findAvailable(used, kind, current):
    if kind == "block" and current < 256: kind = "blocktg" # preserve <256 requirement for likely terrain gen blocks
    for i in ID_RANGES[kind]:
        if i not in used:
            return i
    print used
    assert False, "all %s are used!" % (kind,)        # if you manage to max out the blocks in legitimate usage, I'd be very interested in your mod collection

def sortModsByPriority(mods, sortedMods):
    def getPriority(m):
        if m.startswith("Minecraft"): return -1
        return sortedMods.index(modanalyzer.getModName(m.replace(".csv", "")))

    mods.sort(cmp=lambda a, b: cmp(getPriority(b[0]), getPriority(a[0])))

"""Get whether this mod list contains a vanilla override, which should not be resolved."""
def vanillaOverride(mods):
    for m in mods:
        if m[0].startswith("Minecraft"):
            return True

    return False

"""Get the assigned ID from the resolution data structure (default if none is assigned)."""
def getAssignedId(resolutions, mod, defaultId):
    newId = resolutions[(mod, defaultId)]
    if newId is None:
        return defaultId
    else:
        return newId

"""Get dictionary of id -> [list of (mods, defaultId)], to detect conflicts (if list of mods > 1, of course)."""
def getConflicts(resolutions):
    sliced = {}
    for mod, defaultId in resolutions:
        assignedId = getAssignedId(resolutions, mod, defaultId)

        if not sliced.has_key(assignedId):
            sliced[assignedId] = []

        sliced[assignedId].append((mod, defaultId))

    return sliced

"""Get a list of edits of tuples (mod,kind,id,newId) to resolve ID conflicts of 'kind'."""
def getConflictResolutions(contents, kind, allSortedMods, preferredIDs):

    # initialize 'resolutions' to (mod, defaultId) -> None (no change)
    # -- this data structure is used to keep track of the assigned IDs being resolved
    resolutions = {}
    unlocalizedName2ID = {}
    for mod, content in contents.iteritems():
        if mod.replace(".csv","") not in allSortedMods: continue
        for defaultId, data in content.get(kind, {}).iteritems():
            resolutions[(mod, modlist.intIfInt(defaultId))] = None

            # also save unlocalized name lookup table, for NEI loading
            if data.has_key("unlocalizedName"):
                name = data["unlocalizedName"]

                if name in ("tile.null", "item.null"): continue # useless

                if unlocalizedName2ID.has_key(name):
                    name += "#" + defaultId  # ambiguous, mostly useless
                unlocalizedName2ID[name] = (mod, defaultId) 

    #print "unlocalizedName2ID"
    #pprint.pprint(unlocalizedName2ID)

    #print "INITIAL RESOLUTIONS"
    #pprint.pprint(resolutions)

    # Load preferred IDs from NEI dump, pre-populating resolutions
    for name, newId in preferredIDs.iteritems():
        m = unlocalizedName2ID.get(name)
        if m is None: m = unlocalizedName2ID.get(name.replace("tile.","")) # sometimes finds more.. (IC2)
        if m is None: m = unlocalizedName2ID.get(name.replace("item.",""))

        if m is not None:  # can't match everything
            mod, defaultId = m
            defaultId = modlist.intIfInt(defaultId)
            if mod.startswith("Minecraft-"): continue # vanilla, uninteresting
            print "Matched preferred ID:",name,"is",(mod, defaultId),"->",newId

            assert resolutions[(mod, defaultId)] is None, "attempted to load preferred ID for %s,%s -> %s but already %s?" % (mod, defaultId, newId, resolutions[(mod, defaultId)])

            resolutions[(mod, defaultId)] = newId

    #print "PRE-POPULATED RESOLUTIONS"
    #pprint.pprint(resolutions)

    conflicts = getConflicts(resolutions)
    #print "SLICED",
    #pprint.pprint(conflicts)

    used = set(conflicts.keys())

    for id, usingMods in conflicts.iteritems():
        if len(usingMods) > 1:
            # sort by priority, highest mods last
            sortedMods = usingMods
            sortModsByPriority(sortedMods, allSortedMods)

            if vanillaOverride(sortedMods):
                continue

            print "Conflict on %s at %s" % (kind, id)

            # move already-resolved IDs to front of the queue, cut in line, ultimate highest priority
            alreadyAssigned = []
            for conflictingMod in sortedMods:
                if resolutions[conflictingMod] is not None:
                    alreadyAssigned.append(conflictingMod)
            assert len(alreadyAssigned) <= 1, "multiple IDs already resolved to same ID? %s on id %s" % (alreadyAssigned, id)

            if len(alreadyAssigned) > 0:
                priorityMod = alreadyAssigned.pop()
                print "\t(using preference %s)" % (priorityMod,)
                del sortedMods[sortedMods.index(priorityMod)]
                sortedMods.append(priorityMod)  # last = highest

            print "\tkeeping %s %s:%s" % (sortedMods.pop(), kind, id)  # it gets the ID

            if kind not in RESOLVE_CONFLICT_KINDS:
                # some conflicts we can't do much about, just alert them
                for conflictingMod in sortedMods:
                    print "\tkeeping %s %s:%s" % (conflictingMod, kind, id)
                continue

            # Move other mods out of the way
            for conflictingMod in sortedMods:
                # first available (one-fit)
                # TODO: bin packing algorithms, for multiple contiguous IDs - first, last, best, worst, almost worst fits
                newId = findAvailable(used, kind, id)
                used.add(newId)

                key = conflictingMod
                assert resolutions.has_key(key), "resolution missing key? %s" % (key,)
                assert resolutions[key] is None, "attempted to resolve already-resolved? %s -> %s but already %s" % (key, resolutions[key], newId)
                resolutions[key] = newId
                print "\tmoving %s %s -> %s" % (conflictingMod, id, newId)

    return resolutions

CONFIG_IGNORE = ["forge.cfg", "forgeChunkLoading.cfg"]  # TODO: exclude from deps in mod analysis

"""Get list of source and target paths for config files of a given mod."""
def getConfigFiles(mod):
    configDir = modanalyzer.getConfigsDir(mod)

    configs = []
    for name in modanalyzer.recursiveListdir(configDir):
        if name in CONFIG_IGNORE: 
            continue
        sourcePath = os.path.join(configDir, name)
        targetPath = os.path.join(modanalyzer.TEST_SERVER_ROOT, "config", name)

        configs.append((sourcePath, targetPath))

    return configs

"""Install mod configuration. Returns any needed manual edits."""
def installModConfigs(mod, modEdits):
    pendingEdits = []

    # read default configs
    editingConfigs = {}
    for sourcePath, targetPath in getConfigFiles(mod):
        data = file(sourcePath).read()
        editingConfigs[targetPath] = data

    # apply edits
    alreadyEditedLines = {}
    for mod, kind, oldId, newId in modEdits:
        success = False
        for targetPath, data in editingConfigs.iteritems():
            data, thisFailed, editedLineText = applyConfigEdit(mod, data, kind, oldId, newId, excludeLineTexts=alreadyEditedLines.get(targetPath, set()))

            editingConfigs[targetPath] = data
            if not thisFailed: 
                success = True

                # record this line as 'we edited it', so we don't edit it again
                if not alreadyEditedLines.has_key(targetPath):
                    alreadyEditedLines[targetPath] = set()
                alreadyEditedLines[targetPath].add(editedLineText)

                break

        if not success:
            #print "MANUAL EDIT",mod,kind,oldId,newId
            pendingEdits.append((mod, kind, oldId, newId))


    # write files
    needsMerge = False
    for targetPath, data in editingConfigs.iteritems():
        print "Installing %s [%s]" % (targetPath, len(modEdits))
        modanalyzer.mkdirContaining(targetPath)

        if os.path.exists(targetPath):
            print "NOTICE: Mod reuses config: installing configs for %s from %s but %s already exists - needs merge" % (mod, sourcePath, targetPath)
            readme = "\n" + ("#" * 70) + "\n# TODO: Merge from " + modanalyzer.getModName(mod) + "\n" + ("#" * 70) + "\n"
            data = readme + data

            needsMerge = True
            pendingEdits += modEdits # probably everything, to be safe
            # TODO: try to merge automatically?

        file(targetPath, "a").write(data)

    return pendingEdits
   
"""Change given ID in read config file data, or add comments for the user to do it if it cannot be automated."""
def applyConfigEdit(mod, data, kind, oldId, newId, excludeLineTexts):
    section = None
    requiresManual = False
    editedLineText = None

    if kind == "item":
        if not mcmodfixes.usesUnshiftedItemIDs(mod):
            # most mods use shifted IDs
            oldId -= 256
            newId -= 256

    # id kinds which might collide with other kinds, restrict ourselves to Forge sections
    mustMatchSection = kind in ("biome")

    # Find possibly matching lines
    hits = {}
    lines = data.split("\n")
    comments = []
    for i, line in enumerate(lines):
        line = line.replace("\n", "")
        if line.startswith("#"): continue # skip comments
        if line in excludeLineTexts: 
            continue # skip lines we ourselves added (avoid transitive edits)

        if line.startswith("%s {" % (kind,)):
            section = kind
    
        if line.endswith("=%s" % (oldId)):
            replacement = re.sub(r"\d+$", str(newId), line)
            assert replacement != line, "Failed to replace matched config line %s for %s -> %s" % (line, oldId, newId)

            matchingSection = section == kind
            if mustMatchSection and not matchingSection: continue  # probably not this

            hits[i] = {"old": line, "new": replacement, "section": section, "matchingSection": matchingSection}

    if len(hits) == 0:
        # couldn't find it
        # TODO: special-case some mods?
        comments.append("# TODO: change %s ID %s -> %s" % (kind, oldId, newId))
        requiresManual = True
    elif len(hits) == 1:
        # just one hit, we know what to do
        n = hits.keys()[0]
        lines[n] = hits[n]["new"]
        editedLineText = lines[n]
        comments.append("# Changed %s: %s -> %s" % (kind, hits[n]["old"], hits[n]["new"]))
    else:
        # ambiguous..
        # TODO: if there is only one matching section, use it! it is not ambiguous
        for n in hits.keys():
            comments.append("# TODO: Change %s -> %s, one of %s ID %s -> %s" % (hits[n]["old"], hits[n]["new"], kind, oldId, newId))
        requiresManual = True

    data = "\n".join(lines + comments)
    return data, requiresManual, editedLineText

"""Get an estimate of the relative amount of the content in a mod."""
def getModGirth(contents, mod):
    key = modanalyzer.getModName(mod) + ".csv"
    if not contents.has_key(key):
        print "No mod analysis found for %s, please analyze" % (mod,)
        sys.exit(-1)

    content = contents[key]

    blocks = content.get("block", [])

    girth = len(blocks) * 1000 + len(content) 
    # TODO: more in-depth analysis, weights for different content types? (blocks > item?)
    # TODO: also factor in id 'immobility', higher priority if can't move?

    return girth

PRIORITY_FILE = "priority.txt"

"""Sort all mods by priority."""
def sortAllMods(contents):
    mods = os.listdir(modanalyzer.ALL_MODS_DIR) 

    # default priority
    mods.sort(cmp=lambda a, b: cmp(getModGirth(contents, b), getModGirth(contents, a)))

    if os.path.exists(PRIORITY_FILE):
        existingPriority = [x.strip() for x in file(PRIORITY_FILE).readlines()]
        missing = set(mods) - set(existingPriority) 
        if len(missing) != 0:
            # just add to the end
            mods = existingPriority + list(missing)
            print "NOTICE: Adding %s mods to end of priority list %s" % (missing, PRIORITY_FILE)
            file(PRIORITY_FILE, "w").write("\n".join(mods))
        else:
            print "Reusing priority file %s" % (PRIORITY_FILE,)
            mods = existingPriority
    else:
        file(PRIORITY_FILE, "w").write("\n".join(mods))
        print "Wrote new priority file at %s, edit as you wish" % (PRIORITY_FILE,)

    return mods


"""Load an NEI id dump into a mapping from unlocalized name to ID, blocks and items."""
def parseNEIDump(fn):
    m = {}
    for line in file(fn).readlines():
        line = line.replace("\n", "")

        if line.startswith("Block. Name: ") or line.startswith("Item. Name: "):
            kind, info = line.split(": ", 1)
            unlocalizedName, id = info.split(". ID: ")
            m[unlocalizedName] = int(id)

    return m
        

"""Load an NEI dump in the current working directory."""
def loadNEIDump():
    found = glob.glob("IDMap dump*")
    if len(found) == 0:
        return {} # no preference
    elif len(found) > 1:
        print "Multiple NEI dumps found. Which one do you want?"
        for i, f in enumerate(found):
            print "%s. %s" % (i + 1, f)
        print ">",
        filename = found[int(raw_input()) - 1]
    else:
        filename = found[0]

    return parseNEIDump(filename)

def filterItemBlocks(contents):
    newContents = {}

    for mod, content in contents.iteritems():
        newContent = {}
        for kind, datas in content.iteritems():
            newDatas = {}
            for defaultId, data in datas.iteritems():
                skip = False
                if data.get("isItemBlock", "false") == "true":
                    skip = True # skip itemblocks (items) - because they're handled as blocks
        
                if not skip:
                    newDatas[defaultId] = data

            newContent[kind] = newDatas
        newContents[mod] = newContent

    return newContents

"""Get list of all mods to include."""
def getWantedMods():
    everything = os.listdir(modanalyzer.ALL_MODS_DIR)

    tag = "# Remove this line to edit your desired mod selection list below\n"
    if os.path.exists(WANTED_MODS_FILENAME) and not file(WANTED_MODS_FILENAME).read().startswith(tag[:-1]):
        # user-selected list found, and edited, use it
        wanted = [line.strip() for line in file(WANTED_MODS_FILENAME).readlines()]
        wanted = [want for want in wanted if not want.startswith("#")]
        print "Using wanted list",WANTED_MODS_FILENAME
    else:
        # assume wants everything, but write out list for customizing mod selection on subsequent runs
        wanted = sorted(everything)
        f = file(WANTED_MODS_FILENAME, "wt")
        f.write(tag)
        for w in wanted:
            f.write(w + "\n")
        f.close()
        print "Wrote wanted list",WANTED_MODS_FILENAME

    return wanted


def main():
    preferredIDs = loadNEIDump()

    contents = modanalyzer.load()
    contents = filterItemBlocks(contents)

    wantedMods = getWantedMods()
    sortedMods = [x for x in sortAllMods(contents) if x in wantedMods]

    resolutionsByKind = {}
    vanilla = "Minecraft-" + modanalyzer.MC_VERSION
    for kind in CHECK_CONFLICT_KINDS:
        resolutionsByKind[kind] = getConflictResolutions(contents, kind, sortedMods+[vanilla], preferredIDs)
    #print "FINAL RES",
    #pprint.pprint(resolutionsByKind)

    modsFolder, coremodsFolder, configFolder = modanalyzer.prepareCleanServerFolders(modanalyzer.TEST_SERVER_ROOT)

    requiresManual = {}
    for modName in sortedMods:
        mod = os.path.join(modanalyzer.ALL_MODS_DIR, modName)

        if not contents.has_key(os.path.basename(mod)+".csv"):
            print "No mod analysis found for %s, please analyze" % (mod,)
            sys.exit(-1)

        print "Installing",mod
        modanalyzer.installMod(mod, modsFolder, coremodsFolder)

        # extract the resolutions we care about, for editing the config
        modEdits = []
        for kind, resolutions in resolutionsByKind.iteritems():
            for (thisMod, defaultId), assignedId in resolutions.iteritems():
                if os.path.basename(mod)+".csv" == thisMod:
                    if assignedId is not None and assignedId != defaultId: # only if changed
                        modEdits.append((mod, kind, defaultId, assignedId))
        #print "MODEDITS=",modEdits 

        pendingEdits = installModConfigs(mod, modEdits)
        if len(pendingEdits) > 0:
            requiresManual[mod] = pendingEdits

    if len(requiresManual) > 0:
        print "=" * 70
        for m, edits in requiresManual.iteritems():
            print m, "\t", " ".join([x[1] for x in getConfigFiles(m)]), "\t", edits
        print "=" * 70
        print "The above mods require manual configuration file editing to continue."
        print "Edit their configs appropriately (search for 'TODO'), then start the server."
    else:
        print "Ready to go..."
        modanalyzer.runServer()

if __name__ == "__main__":
    main()
