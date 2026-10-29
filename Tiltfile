# Entry point for the local kind cluster.
# Every component ships deploy/<component>/module.yaml and deploy/<component>/Tiltfile.module (module contract),
# and a profile from deploy/tilt/profiles.yaml decides which modules are enabled.
#
# Tilt treats every kind-* context as local, so the guard below pins the one cluster this repository owns.
# Each Tiltfile.module registers its own resources, labelled with the module's group.

CONTEXT = 'kind-wdf'

config.define_string('profile')
config.define_string('modules_dir')
cfg = config.parse()

profile = cfg.get('profile', 'ci')
modules_dir = cfg.get('modules_dir', 'deploy')

if k8s_context() != CONTEXT:
    fail('refusing context %s, only %s is allowed' % (k8s_context(), CONTEXT))

profiles = read_yaml('deploy/tilt/profiles.yaml')['profiles']
if profile not in profiles:
    fail('unknown profile %s, expected one of %s' % (profile, sorted(profiles.keys())))
groups = profiles[profile]['groups']
# A profile may reuse another profile's module selection, which is how soak runs the ci modules without live update.
module_profile = profiles[profile].get('modules', profile)

# Library functions read the mode from the environment because a loaded file cannot see Tiltfile globals.
os.putenv('WDF_LIVE_UPDATE', '1' if profiles[profile].get('live_update', False) else '0')

# Components sit at most two levels down (deploy/dummy/<group>), so fixtures under deploy/tilt are skipped.
root = os.path.abspath(modules_dir) + '/'
manifests = sorted([
    f for f in listdir(modules_dir, recursive=True)
    if f.endswith('/module.yaml') and f.startswith(root) and f[len(root):].count('/') in [1, 2]
])

modules = []
for path in manifests:
    # The loader exits non-zero with `reason: <code>: <detail>`, which local() reports in the failure.
    local(['contracts/module/check.sh', path], quiet=True)
    module = read_yaml(path)
    module['dir'] = path[:-len('/module.yaml')]
    if module['group'] in groups and module_profile in module['profiles']:
        modules.append(module)

enabled = [m['name'] for m in modules]
for module in modules:
    deps = [d.split('/')[1] for d in module.get('depends_on', [])]
    missing = [d for d in deps if d not in enabled]
    if missing:
        fail('module %s depends on %s, which profile %s does not enable' % (module['name'], missing, profile))
    load_dynamic(module['dir'] + '/Tiltfile.module')

config.set_enabled_resources(enabled)
