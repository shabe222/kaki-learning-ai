/**
 * 语音通话里 DARYA 用哪个声音。和主题偏好同一套形状：按用户存一份，再存一份 last。
 *
 * 为什么要有它：后端 `/api/video/speech` 本来就收 `voice` 参数，一路传到
 * `edge_tts.Communicate(text, voice)`；只有前端把它写死成 `zh-CN-XiaoxiaoNeural`，
 * 而且产品里没有任何地方能改。于是「换一个声音」这件事，模型和学生都做不到。
 *
 * 名单是白名单，前后端各留一份：后端要挡的是「随便传一个字符串进 edge_tts」，
 * 那不会报错，只会让用户听到一个莫名其妙的声音或者干脆没声。
 */
export type DaryaVoiceId =
  | 'zh-CN-XiaoxiaoNeural'
  | 'zh-CN-XiaoyiNeural'
  | 'zh-CN-YunxiNeural'
  | 'zh-CN-YunyangNeural';

export interface DaryaVoiceOption {
  id: DaryaVoiceId;
  label: string;
  desc: string;
  /** 只用来在设置里给一个可看的标记；后端另有一份同名映射做备用音色的同性别回退。 */
  gender: 'female' | 'male';
}

export const DARYA_VOICES: DaryaVoiceOption[] = [
  { id: 'zh-CN-XiaoxiaoNeural', label: '晓晓', desc: '女声，温和清楚', gender: 'female' },
  { id: 'zh-CN-XiaoyiNeural', label: '晓伊', desc: '女声，年轻一点', gender: 'female' },
  { id: 'zh-CN-YunxiNeural', label: '云希', desc: '男声，自然', gender: 'male' },
  { id: 'zh-CN-YunyangNeural', label: '云扬', desc: '男声，沉稳', gender: 'male' },
];

export const DEFAULT_DARYA_VOICE: DaryaVoiceId = 'zh-CN-XiaoxiaoNeural';

/**
 * 语速与音调。取值是「相对默认的百分比 / 赫兹」，由前端选、后端校验范围后拼成
 * edge_tts 的语法——不让界面直接写 `+15%` 这种字符串，写错了不会报错只会听起来怪。
 *
 * 语速对学生是真有用的那一项（听不清的地方可以放慢）；音调只给三档，
 * 再宽就会把神经语音调成不像人。
 */
export type DaryaSpeedId = 'slow' | 'normal' | 'fast';
export type DaryaPitchId = 'low' | 'normal' | 'high';

export const DARYA_SPEEDS: Array<{ id: DaryaSpeedId; label: string; rate: number }> = [
  { id: 'slow', label: '慢一点', rate: -15 },
  { id: 'normal', label: '正常', rate: 0 },
  { id: 'fast', label: '快一点', rate: 15 },
];

export const DARYA_PITCHES: Array<{ id: DaryaPitchId; label: string; pitch: number }> = [
  { id: 'low', label: '低一点', pitch: -20 },
  { id: 'normal', label: '正常', pitch: 0 },
  { id: 'high', label: '高一点', pitch: 20 },
];

export function daryaRateOf(speed: DaryaSpeedId): number {
  return DARYA_SPEEDS.find((option) => option.id === speed)?.rate ?? 0;
}

export function daryaPitchOf(pitch: DaryaPitchId): number {
  return DARYA_PITCHES.find((option) => option.id === pitch)?.pitch ?? 0;
}

const STORAGE_PREFIX = 'darya_voice_v1';
const LAST_VOICE_KEY = `${STORAGE_PREFIX}:last`;

function storageKey(userId?: number | null) {
  return `${STORAGE_PREFIX}:${userId ?? 'guest'}`;
}

function isKnownVoice(value: string | null): value is DaryaVoiceId {
  return !!value && DARYA_VOICES.some((voice) => voice.id === value);
}

export function readDaryaVoice(userId?: number | null): DaryaVoiceId {
  const stored = userId == null
    ? localStorage.getItem(LAST_VOICE_KEY)
    : localStorage.getItem(storageKey(userId)) ?? localStorage.getItem(LAST_VOICE_KEY);
  return isKnownVoice(stored) ? stored : DEFAULT_DARYA_VOICE;
}

export function persistDaryaVoice(voice: DaryaVoiceId, userId?: number | null) {
  localStorage.setItem(storageKey(userId), voice);
  localStorage.setItem(LAST_VOICE_KEY, voice);
}

const SPEED_PREFIX = 'darya_speed_v1';
const PITCH_PREFIX = 'darya_pitch_v1';

function simpleStorageKey(prefix: string, userId?: number | null) {
  return `${prefix}:${userId ?? 'guest'}`;
}

export function readDaryaSpeed(userId?: number | null): DaryaSpeedId {
  const stored = localStorage.getItem(simpleStorageKey(SPEED_PREFIX, userId))
    ?? localStorage.getItem(`${SPEED_PREFIX}:last`);
  return stored === 'slow' || stored === 'fast' ? stored : 'normal';
}

export function persistDaryaSpeed(speed: DaryaSpeedId, userId?: number | null) {
  localStorage.setItem(simpleStorageKey(SPEED_PREFIX, userId), speed);
  localStorage.setItem(`${SPEED_PREFIX}:last`, speed);
}

export function readDaryaPitch(userId?: number | null): DaryaPitchId {
  const stored = localStorage.getItem(simpleStorageKey(PITCH_PREFIX, userId))
    ?? localStorage.getItem(`${PITCH_PREFIX}:last`);
  return stored === 'low' || stored === 'high' ? stored : 'normal';
}

export function persistDaryaPitch(pitch: DaryaPitchId, userId?: number | null) {
  localStorage.setItem(simpleStorageKey(PITCH_PREFIX, userId), pitch);
  localStorage.setItem(`${PITCH_PREFIX}:last`, pitch);
}

export function daryaVoiceLabel(voice: DaryaVoiceId): string {
  return DARYA_VOICES.find((option) => option.id === voice)?.label ?? voice;
}
