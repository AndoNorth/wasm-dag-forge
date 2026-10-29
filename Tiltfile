# Entry point for the local kind cluster.
# Every component ships deploy/<component>/module.yaml and deploy/<component>/Tiltfile.module (module contract),
# and a profile from deploy/tilt/profiles.yaml decides which modules are enabled.
#
# Tilt treats every kind-* context as local, so the guard below pins the one cluster this repository owns.
# `register(module)` in each Tiltfile.module creates the module's resources with the module's group as label,
# and `module['resource_deps']` lists the enabled resources it waits for.

CONTEXT = 'kind-wdf'

config.define_string('profile')
config.define_string('modules_dir')
config.define_string('versions')
cfg = config.parse()

profile = cfg.get('profile', 'ci')
modules_dir = cfg.get('modules_dir', 'deploy')
versions = cfg.get('versions', 'deploy/versions.yaml')

if k8s_context() != CONTEXT:
    fail('refusing context %s, only %s is allowed' % (k8s_context(), CONTEXT))

profiles = read_yaml('deploy/tilt/profiles.yaml')['profiles']
if profile not in profiles:
    fail('unknown profile %s, expected one of %s' % (profile, sorted(profiles.keys())))
groups = profiles[profile]['groups']

# One level only, so fixtures nested under deploy/tilt are never picked up as components.
root = os.path.abspath(modules_dir) + '/'
manifests = sorted([
    f for f in listdir(modules_dir, recursive=True)
    if f.endswith('/module.yaml') and f.startswith(root) and f[len(root):].count('/') == 1
])

modules = []
for path in manifests:
    # The loader exits non-zero with `reason: <code>: <detail>`, which local() reports in the failure.
    local(['contracts/module/check.sh', path], env={'WDF_VERSIONS': versions}, quiet=True)
    module = read_yaml(path)
    module['dir'] = path[:-len('/module.yaml')]
    if module['group'] in groups and profile in module['profiles']:
        modules.append(module)

enabled = [m['name'] for m in modules]
for module in modules:
    deps = [d.split('/')[1] for d in module.get('depends_on', [])]
    missing = [d for d in deps if d not in enabled]
    if missing:
        fail('module %s depends on %s, which profile %s does not enable' % (module['name'], missing, profile))
    module['resource_deps'] = deps
    load_dynamic(module['dir'] + '/Tiltfile.module')['register'](module)

config.set_enabled_resources(enabled)
