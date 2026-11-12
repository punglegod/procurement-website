import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

// 验证安全升级后上层库实际使用的接口，全部文件位于独立临时目录。
const require = createRequire(new URL('../../frontend/package.json', import.meta.url))
const glob = require('glob'), editorconfig = require('editorconfig'), beautify = require('js-beautify')
const tempBase = realpathSync(tmpdir())
const temp = mkdtempSync(path.join(tempBase, 'procurement-stage7-deps-'))
try {
  writeFileSync(path.join(temp, '.editorconfig'), 'root = true\n[*.js]\nindent_style = space\nindent_size = 4\n')
  writeFileSync(path.join(temp, 'sample.js'), 'if(true){console.log(1);}\n')
  assert.deepEqual(glob.globSync('*.js', { cwd: temp }), ['sample.js'])
  assert.equal(editorconfig.parseSync(path.join(temp, 'sample.js')).indent_size, 4)
  assert.match(beautify.html('<div><p>safe</p></div>'), /<p>safe<\/p>/)
  const cli = path.join(path.dirname(require.resolve('js-beautify/package.json')), 'js/bin/js-beautify.js')
  const child = spawnSync(process.execPath, [cli, '--editorconfig', '--type', 'js', '*.js'], { cwd: temp, encoding: 'utf8' })
  assert.equal(child.status, 0, child.stderr)
  assert.match(readFileSync(path.join(temp, 'sample.js'), 'utf8'), /\n {4}console\.log\(1\);/)
  console.log(JSON.stringify({
    glob: require('glob/package.json').version,
    minimatch: require('minimatch/package.json').version,
    editorconfig: require('editorconfig/package.json').version,
    glob_matching: 'passed', editorconfig_minimatch: 'passed', beautifier_html: 'passed', beautifier_cli_glob_editorconfig: 'passed',
  }))
} finally {
  // 删除前确认实际路径仍在预期临时根目录的直接子目录内。
  const resolved = realpathSync(temp)
  assert.equal(path.dirname(resolved), tempBase)
  assert.ok(path.basename(resolved).startsWith('procurement-stage7-deps-'))
  rmSync(resolved, { recursive: true, force: true })
}
