#!/usr/bin/env node
/**
 * index.html 里 CORE 块的测试：node test_core.mjs
 *
 * 重点不是"函数能跑"，而是**前端汇总口径和服务端（build_resources.py）完全一致** ——
 * 用户在网页里抓完一个 UP 主后，前端会立刻把结果并进当前页面（不重新加载），
 * 如果两边的分类/时长/统计算法有一点不一样，页面显示的数字就会和刷新后对不上。
 * 所以这里拿真实的 data/resources.json 反过来验算：JS 重算的结果必须逐字段等于 Python 产出的结果。
 */
import { readFileSync } from 'node:fs';

const HERE = new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const read = (p) => readFileSync(HERE + p, 'utf8');

let PASS = 0, FAIL = 0;
function ok(name, cond, extra = '') {
  if (cond) { PASS++; console.log('  \u2713 ' + name + (extra ? '  ' + extra : '')); }
  else { FAIL++; console.log('  \u2717 ' + name + '  ' + extra); }
}

const html = read('index.html');
const m = html.match(/\/\* === CORE:START === \*\/([\s\S]*?)\/\* === CORE:END === \*\//);
if (!m) { console.error('找不到 CORE 块（index.html 里的 /* === CORE:START === */ 标记）'); process.exit(1); }
const core = new Function(m[1] + '\nreturn {clock, humanDuration, fmtNum, recomputeAggregates, mergeSlice, buildOverview};')();
/** CORE 块外的 module.exports 导出块：拼回 CORE 文本后跑一遍，验证真的导出了 buildOverview */
const exportAt = html.indexOf('if (typeof module');
const exportBlock = (function () {
  const tail = html.slice(exportAt);
  const end = tail.search(/\r?\n\s*\}\r?\n/);
  return end < 0 ? '' : tail.slice(0, end + tail.slice(end).search(/\}/) + 1);
})();
const exported = new Function('module', m[1] + '\n' + exportBlock + '\nreturn module.exports;')({ exports: {} });

/** 键顺序无关的深比较（数组顺序仍然有意义） */
function stable(v) {
  if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
  if (v && typeof v === 'object') {
    return '{' + Object.keys(v).sort().map((k) => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
  }
  return JSON.stringify(v === undefined ? null : v);
}

console.log('\n[1] 格式化函数（与 build_resources.py 同口径）');
const clockCases = [[0, '0:00'], [59, '0:59'], [60, '1:00'], [3599, '59:59'], [3600, '1:00:00'],
                    [3661, '1:01:01'], [409643, '113:47:23']];
ok('clock 边界值', clockCases.every(([s, want]) => core.clock(s) === want),
   clockCases.map(([s]) => s + '→' + core.clock(s)).join(' '));
ok('clock 负数/非法值当 0', core.clock(-5) === '0:00' && core.clock(null) === '0:00');
const humCases = [[0, '0 秒'], [59, '59 秒'], [60, '1 分钟'], [3660, '1 小时 1 分'], [3600, '1 小时'],
                  [7200, '2 小时'], [1894 * 3600, '1894 小时'], [10800, '3 小时']];
ok('humanDuration 边界值', humCases.every(([s, want]) => core.humanDuration(s) === want),
   humCases.map(([s]) => s + '→' + core.humanDuration(s)).join(' '));
ok('fmtNum 万/亿', core.fmtNum(9999) === '9999' && core.fmtNum(10000) === '1万'
   && core.fmtNum(244915751) === '2.4亿' && core.fmtNum(2205000) === '220.5万',
   [core.fmtNum(9999), core.fmtNum(10000), core.fmtNum(2205000), core.fmtNum(244915751)].join(' / '));

console.log('\n[2] 空输入');
const empty = core.recomputeAggregates([], [], [], []);
ok('空数据不崩', empty.categories.length === 0 && empty.stats.total === 0 && empty.stats.duration_text === '0 秒');
ok('空数据的 stats 字段齐全',
   ['total', 'courses', 'categories', 'sources', 'subjects', 'duration_sec', 'duration_text',
    'bundles', 'lessons', 'lesson_duration_sec', 'lesson_duration_text', 'bundle_threshold_sec']
     .every((k) => k in empty.stats), Object.keys(empty.stats).join(','));

console.log('\n[3] 用真实数据验算：JS 重算 == Python 产出');
let data;
try { data = JSON.parse(read('data/resources.json')); }
catch (e) { console.log('  - 读不到 data/resources.json，跳过（先跑 python build_resources.py）'); data = null; }

if (data) {
  const r = core.recomputeAggregates(data.videos, data.courses, data.sources, data.subjects);
  ok('分类列表完全一致（' + data.categories.length + ' 个）', stable(r.categories) === stable(data.categories),
     stable(r.categories) === stable(data.categories) ? '' : '前几项不一致');
  ok('课程列表完全一致（' + data.courses.length + ' 个）', stable(r.courses) === stable(data.courses));
  ok('来源统计完全一致（' + data.sources.length + ' 个）', stable(r.sources) === stable(data.sources));
  ok('学科统计完全一致（' + data.subjects.length + ' 个，含 0 视频的内置学科）',
     stable(r.subjects) === stable(data.subjects),
     stable(r.subjects) === stable(data.subjects) ? '' :
       JSON.stringify(r.subjects.find((s) => s.count)) + ' vs ' + JSON.stringify(data.subjects.find((s) => s.count)));
  ok('全局统计完全一致', stable(r.stats) === stable(data.stats),
     stable(r.stats) === stable(data.stats) ? '' : JSON.stringify(r.stats) + ' vs ' + JSON.stringify(data.stats));
  ok('总时长 = 所有视频时长之和',
     r.stats.duration_sec === data.videos.reduce((a, v) => a + v.duration, 0));
  ok('单课 + 课程包数量自洽', r.stats.bundles + r.stats.lessons === r.stats.total);
  ok('分类计数之和 = 视频总数',
     r.categories.reduce((a, c) => a + c.count, 0) === r.stats.total);
  ok('课程计数之和 = 视频总数',
     r.courses.reduce((a, c) => a + c.count, 0) === r.stats.total);
  ok('学科计数之和 = 视频总数',
     r.subjects.reduce((a, s) => a + s.count, 0) === r.stats.total);
  ok('stats.subjects 只数有视频的学科',
     r.stats.subjects === r.subjects.filter((s) => s.count).length, String(r.stats.subjects));
  ok('内置学科顺序与数量被保留（语文…地理 + 其他）',
     r.subjects.filter((s) => s.builtin).length === 10 && r.subjects[0].id === 'chinese',
     r.subjects.map((s) => s.id).join(','));
  ok('每个学科的 sources 数 = 该学科下的来源数',
     r.subjects.every((s) => s.sources === new Set(
       data.videos.filter((v) => v.subject_id === s.id).map((v) => v.source_id)).size));

  console.log('\n[4] mergeSlice：并进一个新来源');
  const slice = {
    generated_at: 1800000000000, generated_at_text: '2027-01-15 08:00',
    source: { id: 'uptest', name: '测试老师', mid: 123456, subject_id: 'math', subject: '数学',
              space: 'https://space.bilibili.com/123456', count: 2, courses: 1,
              duration_sec: 1800, duration_text: '30 分钟', bundles: 0, lessons: 2 },
    courses: [{ id: 'uptest:1', season_id: '1', title: '试听课', intro: '', cover: '',
                source_id: 'uptest', author: '测试老师', subject_id: 'math', subject: '数学',
                count: 2, duration_sec: 1800, duration_text: '30 分钟' }],
    categories: [{ id: 'math:hanshu', name: '函数与导数', subject_id: 'math', subject: '数学',
                   count: 2, duration_sec: 1800, duration_text: '30 分钟' }],
    videos: [1, 2].map((i) => ({
      id: 'BVtest' + i, bvid: 'BVtest' + i, title: '【导数】测试' + i,
      url: 'https://www.bilibili.com/video/BVtest' + i, cover: '', duration: 900,
      duration_text: '15:00', duration_label: '15:00', is_bundle: false, views: 10, danmaku: 1,
      pubdate: 1700000000, date_text: '2023-11-15', source_id: 'uptest', author: '测试老师',
      subject_id: 'math', subject: '数学', course_id: 'uptest:1', course_title: '试听课',
      category_id: 'math:hanshu', category: '函数与导数', tags: ['导数'], order: i - 1
    })),
    stats: { total: 2 }
  };
  const merged = core.mergeSlice(data, slice);
  ok('视频数 = 原有 + 新增', merged.videos.length === data.videos.length + 2,
     merged.videos.length + ' vs ' + (data.videos.length + 2));
  ok('新来源出现在来源列表里',
     merged.sources.some((s) => s.id === 'uptest' && s.count === 2 && s.courses === 1));
  ok('原有来源没被改坏', merged.sources.filter((s) => s.id !== 'uptest').every((s, i) =>
     s.name === data.sources[i].name && s.count === data.sources[i].count));
  ok('学科清单被带上（内置学科一个不少）',
     merged.subjects.length >= data.subjects.length
     && merged.subjects.filter((s) => s.builtin).length === 10);
  ok('新增学科的计数进了对应学科',
     merged.subjects.filter((s) => s.id === 'math')[0].count
     === data.subjects.filter((s) => s.id === 'math')[0].count + 2);
  ok('分类计数包含了新视频',
     merged.categories.filter((c) => c.id === 'math:hanshu')[0].count
     === data.categories.filter((c) => c.id === 'math:hanshu')[0].count + 2);
  ok('课程列表含新课程',
     merged.courses.some((c) => c.id === 'uptest:1' && c.count === 2));
  ok('全局统计被重算', merged.stats.total === merged.videos.length
     && merged.stats.sources === merged.sources.length);
  ok('order 被重排成连续序号（新来源排在最后）',
     merged.videos.every((v, i) => v.order === i)
     && merged.videos[merged.videos.length - 1].source_id === 'uptest');
  ok('generated_at_text 用切片里的时间', merged.generated_at_text === '2027-01-15 08:00');

  console.log('\n[4b] mergeSlice：切片带来自定义学科（清单里没有的）');
  const customVids = data.videos.slice(0, 3).map((v, i) => {
    const c = JSON.parse(JSON.stringify(v));
    c.id = 'CUS' + i; c.bvid = 'CUS' + i; c.source_id = 'upcustom'; c.author = '自定义老师';
    c.subject_id = 'custom-x1'; c.subject = '信息技术'; c.course_id = 'upcustom:c';
    c.course_title = '自定义课'; c.category_id = 'custom-x1:qita'; c.category = '其他';
    return c;
  });
  const customSlice = {
    generated_at: Date.now(), generated_at_text: '2027-01-15 09:00',
    source: { id: 'upcustom', name: '自定义老师', mid: 777, subject_id: 'custom-x1',
              subject: '信息技术', space: '', count: 3, courses: 1, duration_sec: 0,
              duration_text: '0 秒', bundles: 0, lessons: 3 },
    courses: [{ id: 'upcustom:c', season_id: null, title: '自定义课', intro: '', cover: '',
                source_id: 'upcustom', author: '自定义老师', subject_id: 'custom-x1',
                subject: '信息技术', count: 3, duration_sec: 0, duration_text: '0 秒' }],
    videos: customVids, categories: [], stats: {}
  };
  const cm = core.mergeSlice(data, customSlice);
  const row = cm.subjects.filter((s) => s.id === 'custom-x1')[0];
  ok('清单里没有的学科被自动补进学科列表', !!row, JSON.stringify(cm.subjects.map((s) => s.id)));
  ok('补进来的学科标成非内置', row && row.builtin === false);
  ok('补进来的学科拿到了切片里的中文名', row && row.name === '信息技术');
  ok('补进来的学科计数正确', row && row.count === 3 && row.sources === 1 && row.courses === 1);
  ok('自定义学科也计入 stats.subjects',
     cm.stats.subjects === cm.subjects.filter((s) => s.count).length, String(cm.stats.subjects));

  console.log('\n[5] mergeSlice：重复抓同一个来源不会变成两份');  const again = core.mergeSlice(merged, slice);
  ok('视频数不变', again.videos.length === merged.videos.length, String(again.videos.length));
  ok('来源数不变', again.sources.length === merged.sources.length);
  ok('课程数不变', again.courses.length === merged.courses.length);

  console.log('\n[6] mergeSlice：替换已有来源（一数被重抓）');
  const one = data.sources[0];
  const reSlice = {
    generated_at: data.generated_at, generated_at_text: data.generated_at_text,
    source: Object.assign({}, one, { count: 1, courses: 1, duration_sec: 100, duration_text: '1 分钟',
                                     bundles: 0, lessons: 1 }),
    courses: [{ id: one.id + ':x', season_id: 'x', title: '重抓的课', intro: '', cover: '',
                source_id: one.id, author: one.name, subject_id: one.subject_id,
                subject: one.subject, count: 1, duration_sec: 100, duration_text: '1 分钟' }],
    videos: [{ id: 'BVnew', bvid: 'BVnew', title: '重抓视频', url: '', cover: '', duration: 100,
               duration_text: '1:40', duration_label: '1:40', is_bundle: false, views: 1, danmaku: 0,
               pubdate: 1700000000, date_text: '2023-11-15', source_id: one.id, author: one.name,
               subject_id: one.subject_id, subject: one.subject, course_id: one.id + ':x',
               course_title: '重抓的课', category_id: one.subject_id + ':qita', category: '其他',
               tags: [], order: 0 }],
    stats: {}
  };
  const replaced = core.mergeSlice(data, reSlice);
  ok('被替换来源的旧视频清空了',
     replaced.videos.filter((v) => v.source_id === one.id).length === 1);
  ok('其它来源的视频没动',
     replaced.videos.filter((v) => v.source_id !== one.id).length
     === data.videos.filter((v) => v.source_id !== one.id).length);
  ok('来源数没变（是替换不是追加）', replaced.sources.length === data.sources.length);
}

console.log('\n[7] buildOverview：导出与空输入');
ok('CORE 块导出了 buildOverview（node 里 new Function 能拿到）',
   typeof core.buildOverview === 'function' && typeof exported.buildOverview === 'function');
ok('空 data 不抛异常，返回空 groups',
   (function () {
     const o = core.buildOverview({});
     return o.total === 0 && o.hours === 0 && o.duration_text === '0 秒'
       && o.groups.length === 0 && o.orphans.videos === 0 && o.orphans.subjects === 0;
   })());
ok('videos 为 null 不抛异常', (function () {
  return core.buildOverview({ videos: null, subjects: [] }).groups.length === 0;
})());
ok('完全没传参也不抛异常', core.buildOverview().groups.length === 0);
ok('空 data 也带全 duration_text/total 等键',
   ['total', 'hours', 'duration_text', 'groups', 'orphans'].every((k) => k in core.buildOverview({})));

console.log('\n[8] buildOverview：构造数据边界');
const mkVideo = (o) => Object.assign({
  id: 'BVx', bvid: 'BVx', title: 't', url: '', cover: '', duration: 600, duration_text: '10:00',
  duration_label: '10 分钟', is_bundle: false, views: 0, danmaku: 0, pubdate: 0, date_text: '',
  source_id: 's1', author: 'a', subject_id: 'math', subject: '数学', course_id: '',
  course_title: '', category_id: 'math:x', category: '函数', tags: [], order: 0
}, o);
const tiny = {
  schema: 'resource-lab/resources/v3',
  sources: [{ id: 's1', name: '甲', mid: 1, subject_id: 'math', subject: '数学', space: 'u/1',
              count: 3, courses: 0, duration_sec: 0, duration_text: '0 秒', bundles: 0, lessons: 3 }],
  subjects: [{ id: 'math', name: '数学', builtin: true, count: 3, sources: 1, courses: 0,
               duration_sec: 0, duration_text: '0 秒' }],
  courses: [],
  videos: [
    mkVideo({ id: 'a', course_id: '', duration: 600 }),
    mkVideo({ id: 'b', course_id: '', duration: 300, category_id: 'math:y', category: '导数' }),
    mkVideo({ id: 'c', course_id: '', duration: 100, category_id: 'math:y', category: '导数',
              is_bundle: true })
  ]
};
const t = core.buildOverview(tiny);
ok('构造数据：groups/分支/计数正确',
   t.groups.length === 1 && t.groups[0].count === 3 && t.groups[0].branches.length === 1
   && t.groups[0].branches[0].count === 3, String(t.groups[0] && t.groups[0].count));
ok('构造数据：course_id 为空字符串的视频不进 courses，但进 categories',
   t.groups[0].branches[0].courses.length === 0
   && t.groups[0].branches[0].categories.reduce((a, c) => a + c.count, 0) === 3);
ok('构造数据：categories 按 count 降序',
   t.groups[0].branches[0].categories.map((c) => c.count).join(',') === '2,1',
   t.groups[0].branches[0].categories.map((c) => c.name + ':' + c.count).join(' '));
ok('构造数据：hours / duration_text 与 humanDuration 同口径',
   t.groups[0].branches[0].hours === Math.round(1000 / 3600)
   && t.groups[0].duration_text === core.humanDuration(1000),
   t.groups[0].duration_text + ' / total=' + t.duration_text);
ok('构造数据：bundles 计数正确', t.groups[0].branches[0].bundles === 1);
ok('构造数据：分支 subjects 来自视频', (function () {
  const s = t.groups[0].branches[0].subjects;
  return s.length === 1 && s[0].id === 'math' && s[0].name === '数学';
})());

const orphanSrc = {
  sources: [{ id: 's9', name: '孤儿老师', mid: 9, subject_id: 'nosuch', subject: '信息技术',
              space: '', count: 1, courses: 0, duration_sec: 0, duration_text: '0 秒', bundles: 0,
              lessons: 1 }],
  subjects: [{ id: 'math', name: '数学', builtin: true, count: 0, sources: 0, courses: 0,
               duration_sec: 0, duration_text: '0 秒' }],
  courses: [],
  videos: [mkVideo({ id: 'z', source_id: 's9', subject_id: 'nosuch', subject: '信息技术',
                     duration: 60 })]
};
const o1 = core.buildOverview(orphanSrc);
ok('来源学科找不到时仍能显示（用 source.subject 兜底建分支）',
   o1.groups.length === 1 && o1.groups[0].branches[0].name === '孤儿老师'
   && o1.groups[0].branches[0].count === 1,
   o1.groups.map((g) => g.id + ':' + g.count).join(' '));
ok('找不到学科的学科名用 source.subject 兜底', o1.groups[0].name === '信息技术', o1.groups[0].name);
ok('视频 subject_id 不在 subjects 里 → 计入 orphans.subjects', o1.orphans.subjects === 1,
   JSON.stringify(o1.orphans));

const unknownSrc = core.buildOverview({
  sources: [], subjects: [], courses: [],
  videos: [mkVideo({ id: 'q', source_id: 'ghost' }), mkVideo({ id: 'r', source_id: 'ghost' })]
});
ok('source_id 找不到来源 → 计入 orphans.videos 且不进 groups',
   unknownSrc.orphans.videos === 2 && unknownSrc.groups.length === 0 && unknownSrc.total === 2,
   JSON.stringify(unknownSrc.orphans) + ' groups=' + unknownSrc.groups.length);

const noDur = core.buildOverview({
  sources: [{ id: 's1', name: '甲', mid: 1, subject_id: 'math', subject: '数学', space: '' }],
  subjects: [{ id: 'math', name: '数学' }], courses: [],
  videos: [mkVideo({ id: 'n1', duration: undefined }), mkVideo({ id: 'n2', duration: null })]
});
ok('duration 缺失按 0 处理，不出现 NaN',
   noDur.hours === 0 && noDur.duration_text === '0 秒'
   && noDur.groups[0].duration_text === '0 秒', noDur.groups[0].duration_text);

const fallbackCourse = core.buildOverview({
  sources: [{ id: 's1', name: '甲', mid: 1, subject_id: 'math', subject: '数学', space: '' }],
  subjects: [{ id: 'math', name: '数学' }], courses: [],          // courses 里找不到这门课
  videos: [mkVideo({ id: 'f1', course_id: 's1:gone', course_title: '缺席的课', duration: 120 }),
           mkVideo({ id: 'f2', course_id: 's1:gone', course_title: '缺席的课', duration: 60 })]
});
ok('课程不在 courses 里时，用视频数兜底 count（3 分钟）',
   fallbackCourse.groups[0].branches[0].courses.length === 1
   && fallbackCourse.groups[0].branches[0].courses[0].count === 2
   && fallbackCourse.groups[0].branches[0].courses[0].title === '缺席的课',
   JSON.stringify(fallbackCourse.groups[0].branches[0].courses));

if (data) {
  console.log('\n[9] buildOverview：真实数据验算');
  const before = JSON.stringify(data);
  const ov = core.buildOverview(data);
  ok('不修改入参（调用前后 JSON.stringify 一致）', before === JSON.stringify(data));
  ok('total === 2738', ov.total === data.videos.length && ov.total === 2738, String(ov.total));
  ok('groups.length === 9（只留有视频的学科，other 被排除）', ov.groups.length === 9,
     ov.groups.map((g) => g.id).join(','));
  ok('groups 之和 === 2738', ov.groups.reduce((a, g) => a + g.count, 0) === 2738);
  ok('学科顺序与 data.subjects 一致',
     ov.groups.map((g) => g.id).join(',')
     === data.subjects.filter((s) => s.count > 0).map((s) => s.id).join(','),
     ov.groups.map((g) => g.id).join(','));
  ok('groups 里没有 count === 0 的学科', ov.groups.every((g) => g.count > 0));
  ok('orphans 全为 0', ov.orphans.videos === 0 && ov.orphans.subjects === 0,
     JSON.stringify(ov.orphans));
  ok('总时长文案 = humanDuration(所有视频时长之和)',
     ov.duration_text === core.humanDuration(data.videos.reduce((a, v) => a + v.duration, 0))
     && ov.hours === Math.round(data.videos.reduce((a, v) => a + v.duration, 0) / 3600),
     ov.duration_text + ' / ' + ov.hours + ' 小时');

  const math = ov.groups.filter((g) => g.id === 'math')[0];
  ok('数学组有 @一数 这个分支',
     !!math && math.branches.some((b) => b.id === 'yishu' && b.name === '一数'));
  ok('数学组分支时长 = humanDuration(该分支视频时长和)',
     math.branches.every((b) => b.duration_text
       === core.humanDuration(data.videos.filter((v) => v.source_id === b.id)
                                            .reduce((a, v) => a + v.duration, 0))));
  ok('每个学科：分支 count 之和 === 学科 count',
     ov.groups.every((g) => g.branches.reduce((a, b) => a + b.count, 0) === g.count),
     ov.groups.map((g) => g.id + ':' + g.branches.reduce((a, b) => a + b.count, 0) + '/' + g.count)
              .join(' '));
  ok('每个学科：duration_text === humanDuration(分支时长之和)',
     ov.groups.every((g) => g.duration_text
       === core.humanDuration(g.branches.reduce((a, b) => a + b.count * 0, 0)
                              + data.videos.filter((v) => g.branches.some((b) => b.id === v.source_id))
                                           .reduce((a, v) => a + v.duration, 0))));

  const phy = ov.groups.filter((g) => g.id === 'physics')[0];
  ok('物理学科有 2 个分支（黄夫人 533 + 一物儿 111 = 644）',
     !!phy && phy.branches.length === 2 && phy.count === 644
     && phy.branches.filter((b) => b.id === 'huangfuren')[0].count === 533
     && phy.branches.filter((b) => b.id === 'yiwuer')[0].count === 111,
     phy ? phy.branches.map((b) => b.name + ':' + b.count).join(' + ') : 'no physics');

  const everyBranch = ov.groups.reduce((a, g) => a.concat(g.branches), []);
  ok('每个分支：courses 之和 + 无课程视频数 === 分支 count',
     everyBranch.every((b) => {
       const withCourse = data.videos.filter((v) => v.source_id === b.id && v.course_id).length;
       return b.courses.reduce((a, c) => a + c.count, 0) + (b.count - withCourse) === b.count;
     }));
  ok('每个分支：categories 之和 === 分支 count',
     everyBranch.every((b) => b.categories.reduce((a, c) => a + c.count, 0) === b.count));
  ok('每个分支：categories 按 count 降序、同数按 name 升序',
     everyBranch.every((b) => b.categories.every((c, i) => i === 0
       || b.categories[i - 1].count > c.count
       || (b.categories[i - 1].count === c.count && b.categories[i - 1].name <= c.name))));
  ok('每个分支：count === 该来源在 data.videos 里的视频数',
     everyBranch.every((b) => b.count
       === data.videos.filter((v) => v.source_id === b.id).length));
  ok('每个分支：bundles === 该来源 is_bundle 视频数',
     everyBranch.every((b) => b.bundles
       === data.videos.filter((v) => v.source_id === b.id && v.is_bundle).length));
  ok('每个分支：courses 的 count/duration_text 取自 data.courses 真实值',
     everyBranch.every((b) => b.courses.every((c) => {
       const real = data.courses.filter((x) => x.id === c.id)[0];
       return !real || (c.count === real.count && c.duration_text === real.duration_text
                        && c.title === real.title);
     })));
  ok('每个分支：courses 之和 === 该分支真实课程视频数（视频都挂在已登记课程上）',
     everyBranch.every((b) => {
       const ids = new Set(data.videos.filter((v) => v.source_id === b.id && v.course_id)
                                      .map((v) => v.course_id));
       return ids.size === b.courses.length;
     }));
  ok('每个分支：subjects 是 [{id,name}]，且含该来源的 subject_id',
     everyBranch.every((b) => Array.isArray(b.subjects) && b.subjects.length >= 1
       && b.subjects.every((s) => s.id && s.name)
       && b.subjects.some((s) => s.id
            === data.sources.filter((x) => x.id === b.id)[0].subject_id)));
  ok('每个分支：mid / space 透传自 data.sources', everyBranch.every((b) => {
    const s = data.sources.filter((x) => x.id === b.id)[0];
    return b.mid === s.mid && b.space === s.space;
  }));
  ok('每个分支：courses 覆盖该来源在 data.courses 里的所有课程',
     everyBranch.every((b) => {
       const want = data.courses.filter((c) => c.source_id === b.id).length;
       return want <= b.courses.length;
     }));
}

console.log('\n===== 结果: ' + PASS + ' 通过 / ' + FAIL + ' 失败 =====');
process.exit(FAIL ? 1 : 0);
