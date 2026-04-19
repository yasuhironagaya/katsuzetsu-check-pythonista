# coding: utf-8
import ui
import json
import os
import datetime
import dialogs
import wave
import struct
import math
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    import editor
except ImportError:
    editor = None

from objc_util import ObjCClass, nsurl


# =========================
# 設定
# =========================
APP_TITLE = '滑舌検査'
DATA_FILE = 'katsuzetsu_records.json'
RECORDINGS_DIR = 'recordings'

SYLLABLES = ['か', 'た', 'ぱ']
DURATIONS = [5, 10]

DEFAULT_FRAME_MS = 20
DEFAULT_THRESHOLD_RATIO = 0.35
DEFAULT_MIN_GAP_MS = 90

AVAudioSession = ObjCClass('AVAudioSession')
AVAudioRecorder = ObjCClass('AVAudioRecorder')
AVAudioPlayer = ObjCClass('AVAudioPlayer')
NSDictionary = ObjCClass('NSDictionary')
NSNumber = ObjCClass('NSNumber')

AUDIO_FORMAT_LINEAR_PCM = 1819304813
AVAudioSessionPortOverrideSpeaker = 1936747378


# =========================
# 保存先関連
# =========================
def resolve_script_folder():
    try:
        if '__file__' in globals():
            path = os.path.abspath(__file__)
            if path:
                return os.path.dirname(path)
    except Exception:
        pass

    if editor is not None:
        try:
            path = editor.get_path()
            if path:
                return os.path.dirname(path)
        except Exception:
            pass

    try:
        path = os.getcwd()
        if path:
            return path
    except Exception:
        pass

    return os.path.expanduser('~/Documents')


def app_file_path(file_name):
    return os.path.join(resolve_script_folder(), file_name)


def recordings_folder_path():
    folder = os.path.join(resolve_script_folder(), RECORDINGS_DIR)
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder


# =========================
# データ保存
# =========================
def load_records():
    path = app_file_path(DATA_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_records(records):
    path = app_file_path(DATA_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# =========================
# WAV解析
# =========================
def estimate_syllable_count_from_wav(
    wav_path,
    frame_ms=DEFAULT_FRAME_MS,
    threshold_ratio=DEFAULT_THRESHOLD_RATIO,
    min_gap_ms=DEFAULT_MIN_GAP_MS
):
    empty_result = {
        'count': 0,
        'peak_times': [],
        'message': '録音ファイルがありません。',
        'max_rms': 0,
        'threshold': 0,
        'times': [],
        'rms_values': [],
        'duration_sec': 0,
        'frame_ms': frame_ms,
        'threshold_ratio': threshold_ratio,
        'min_gap_ms': min_gap_ms,
    }

    if not wav_path or not os.path.exists(wav_path):
        return empty_result

    try:
        with wave.open(wav_path, 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sampwidth != 2:
            empty_result['message'] = f'16bit PCMではありません。sampwidth={sampwidth}'
            return empty_result

        total_samples = len(raw) // 2
        if total_samples == 0:
            empty_result['message'] = '録音データが空です。'
            return empty_result

        samples = struct.unpack('<' + 'h' * total_samples, raw)

        if n_channels == 2:
            samples = samples[::2]

        duration_sec = len(samples) / float(framerate)
        frame_size = max(1, int(framerate * frame_ms / 1000))

        rms_values = []
        times = []

        for start in range(0, len(samples), frame_size):
            chunk = samples[start:start + frame_size]
            if not chunk:
                continue

            energy_sum = 0.0
            for v in chunk:
                energy_sum += v * v

            rms = math.sqrt(energy_sum / len(chunk))
            rms_values.append(rms)
            times.append(start / float(framerate))

        if not rms_values:
            empty_result['message'] = '音量データを作れませんでした。'
            return empty_result

        max_rms = max(rms_values)
        threshold = max_rms * threshold_ratio
        min_gap_sec = min_gap_ms / 1000.0

        peak_times = []
        last_peak_time = -999.0

        for i in range(1, len(rms_values) - 1):
            curr_time = times[i]
            curr_value = rms_values[i]

            is_peak = (
                curr_value >= threshold and
                curr_value >= rms_values[i - 1] and
                curr_value >= rms_values[i + 1]
            )

            if not is_peak:
                continue

            if curr_time - last_peak_time >= min_gap_sec:
                peak_times.append(curr_time)
                last_peak_time = curr_time

        return {
            'count': len(peak_times),
            'peak_times': peak_times,
            'message': (
                f'推定回数: {len(peak_times)}回\n'
                f'最大音量: {max_rms:.1f}\n'
                f'しきい値: {threshold:.1f}\n'
                f'frame_ms: {frame_ms}\n'
                f'threshold_ratio: {threshold_ratio}\n'
                f'min_gap_ms: {min_gap_ms}'
            ),
            'max_rms': max_rms,
            'threshold': threshold,
            'times': times,
            'rms_values': rms_values,
            'duration_sec': duration_sec,
            'frame_ms': frame_ms,
            'threshold_ratio': threshold_ratio,
            'min_gap_ms': min_gap_ms,
        }

    except Exception as e:
        empty_result['message'] = f'解析エラー: {e}'
        return empty_result


def create_matplotlib_graph_image(analysis_result, wide=False):
    times = analysis_result.get('times', [])
    rms_values = analysis_result.get('rms_values', [])
    threshold = analysis_result.get('threshold', 0)
    peak_times = analysis_result.get('peak_times', [])
    count = analysis_result.get('count', 0)

    if not times or not rms_values:
        return None

    fd, image_path = tempfile.mkstemp(suffix='.png')
    os.close(fd)

    if wide:
        figsize = (18, 5)
        dpi = 180
    else:
        figsize = (10, 5)
        dpi = 150

    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(111)

    ax.plot(times, rms_values, label='Volume')
    ax.axhline(threshold, linestyle='--', label='Threshold')

    for i, peak_time in enumerate(peak_times):
        if i == 0:
            ax.axvline(peak_time, linestyle=':', label='Detected Peak')
        else:
            ax.axvline(peak_time, linestyle=':')

    ax.set_title(f'Analysis Graph (estimated count: {count})')
    ax.set_xlabel('Time (sec)')
    ax.set_ylabel('RMS Volume')
    ax.grid(True, alpha=0.3)
    ax.legend()

    fig.tight_layout()
    fig.savefig(image_path)
    plt.close(fig)

    return image_path


# =========================
# 録音・再生
# =========================
class AudioManager(object):
    def __init__(self):
        self.session = AVAudioSession.sharedInstance()
        self.recorder = None
        self.player = None
        self.current_file_path = None

    def setup_record_session(self):
        err = None
        self.session.setCategory_error_('AVAudioSessionCategoryPlayAndRecord', err)
        self.session.setActive_error_(True, err)

    def setup_playback_session(self):
        err = None
        self.session.setCategory_error_('AVAudioSessionCategoryPlayAndRecord', err)
        self.session.setActive_error_(True, err)

        try:
            self.session.overrideOutputAudioPort_error_(
                AVAudioSessionPortOverrideSpeaker, None
            )
        except Exception:
            pass

    def make_record_file_path(self, syllable):
        now_text = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f'{syllable}_{now_text}.wav'
        return os.path.join(recordings_folder_path(), file_name)

    def start_recording(self, syllable):
        try:
            self.stop_playback()
            self.setup_record_session()

            file_path = self.make_record_file_path(syllable)
            url = nsurl(file_path)

            settings = NSDictionary.dictionaryWithDictionary_({
                'AVFormatIDKey': NSNumber.numberWithInt_(AUDIO_FORMAT_LINEAR_PCM),
                'AVSampleRateKey': NSNumber.numberWithFloat_(16000.0),
                'AVNumberOfChannelsKey': NSNumber.numberWithInt_(1),
                'AVLinearPCMBitDepthKey': NSNumber.numberWithInt_(16),
                'AVLinearPCMIsBigEndianKey': NSNumber.numberWithBool_(False),
                'AVLinearPCMIsFloatKey': NSNumber.numberWithBool_(False),
            })

            self.recorder = AVAudioRecorder.alloc().initWithURL_settings_error_(
                url, settings, None
            )

            if not self.recorder:
                return False, '録音オブジェクトを作成できませんでした。'

            self.recorder.prepareToRecord()
            ok = self.recorder.record()

            if not ok:
                self.recorder = None
                return False, '録音を開始できませんでした。マイク権限をご確認ください。'

            self.current_file_path = file_path
            return True, file_path

        except Exception as e:
            self.recorder = None
            return False, f'録音開始エラー: {e}'

    def stop_recording(self):
        try:
            if self.recorder:
                self.recorder.stop()
                self.recorder = None
            return True
        except Exception:
            return False

    def play_file(self, file_path):
        try:
            if not file_path or not os.path.exists(file_path):
                return False, '再生する録音ファイルがありません。'

            self.stop_playback()
            self.setup_playback_session()

            url = nsurl(file_path)
            self.player = AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)

            if not self.player:
                return False, '再生オブジェクトを作成できませんでした。'

            self.player.setVolume_(1.0)
            self.player.prepareToPlay()
            ok = self.player.play()

            if not ok:
                self.player = None
                return False, '再生を開始できませんでした。'

            return True, '再生開始'

        except Exception as e:
            self.player = None
            return False, f'再生エラー: {e}'

    def stop_playback(self):
        try:
            if self.player:
                self.player.stop()
                self.player = None
        except Exception:
            pass


# =========================
# 横スクロール用・大きなグラフビュー
# =========================
class WideScrollableImageView(ui.View):
    def __init__(self, image_path, title='横スクロール拡大グラフ'):
        super().__init__()
        self.name = title
        self.background_color = 'white'
        self.image_path = image_path

        self.info_label = ui.Label()
        self.info_label.alignment = ui.ALIGN_CENTER
        self.info_label.font = ('<System>', 13)
        self.info_label.number_of_lines = 0
        self.info_label.text = '左右にスクロールして細部を確認してください'
        self.add_subview(self.info_label)

        self.scroll_view = ui.ScrollView()
        self.scroll_view.always_bounce_horizontal = True
        self.scroll_view.always_bounce_vertical = False
        self.scroll_view.shows_horizontal_scroll_indicator = True
        self.scroll_view.shows_vertical_scroll_indicator = False
        self.add_subview(self.scroll_view)

        self.image_view = ui.ImageView()
        self.image_view.content_mode = ui.CONTENT_SCALE_TO_FILL
        self.image_view.background_color = 'white'
        self.scroll_view.add_subview(self.image_view)

        self.close_button = ui.Button(title='閉じる')
        self.close_button.font = ('<System-Bold>', 18)
        self.close_button.background_color = '#eeeeee'
        self.close_button.corner_radius = 10
        self.close_button.action = self.close_self
        self.add_subview(self.close_button)

        if self.image_path and os.path.exists(self.image_path):
            self.image_view.image = ui.Image.named(self.image_path)

    def layout(self):
        padding = 10
        info_h = 34
        button_h = 40

        self.info_label.frame = (
            padding, padding, self.width - padding * 2, info_h
        )

        scroll_y = padding + info_h + 4
        scroll_h = self.height - scroll_y - button_h - padding * 2

        self.scroll_view.frame = (
            padding, scroll_y, self.width - padding * 2, scroll_h
        )

        visible_w = self.scroll_view.width
        visible_h = self.scroll_view.height

        image_w = max(visible_w * 2.2, visible_w + 1)
        image_h = visible_h

        self.image_view.frame = (0, 0, image_w, image_h)
        self.scroll_view.content_size = (image_w, image_h)

        self.close_button.frame = (
            padding,
            self.height - button_h - padding,
            self.width - padding * 2,
            button_h
        )

    def close_self(self, sender):
        self.close()


# =========================
# 拡大画像ビュー（通常拡大）
# =========================
class LargeImageView(ui.View):
    def __init__(self, image_path, title='拡大グラフ'):
        super().__init__()
        self.name = title
        self.background_color = 'white'
        self.image_path = image_path

        self.image_view = ui.ImageView()
        self.image_view.content_mode = ui.CONTENT_SCALE_ASPECT_FIT
        self.image_view.background_color = 'white'
        self.add_subview(self.image_view)

        self.close_button = ui.Button(title='閉じる')
        self.close_button.font = ('<System-Bold>', 18)
        self.close_button.background_color = '#eeeeee'
        self.close_button.corner_radius = 10
        self.close_button.action = self.close_self
        self.add_subview(self.close_button)

        if self.image_path and os.path.exists(self.image_path):
            self.image_view.image = ui.Image.named(self.image_path)

    def layout(self):
        padding = 10
        button_h = 40

        self.image_view.frame = (
            padding,
            padding,
            self.width - padding * 2,
            self.height - button_h - padding * 3
        )

        self.close_button.frame = (
            padding,
            self.height - button_h - padding,
            self.width - padding * 2,
            button_h
        )

    def close_self(self, sender):
        self.close()


# =========================
# matplotlib画像表示ビュー
# =========================
class AnalysisGraphView(ui.View):
    def __init__(self, image_path, analysis_result, wide_image_path=None, title='解析グラフ'):
        super().__init__()
        self.name = title
        self.background_color = 'white'
        self.image_path = image_path
        self.wide_image_path = wide_image_path
        self.analysis_result = analysis_result

        self.image_view = ui.ImageView()
        self.image_view.content_mode = ui.CONTENT_SCALE_ASPECT_FIT
        self.image_view.background_color = 'white'
        self.add_subview(self.image_view)

        self.info_label = ui.Label()
        self.info_label.number_of_lines = 0
        self.info_label.alignment = ui.ALIGN_CENTER
        self.info_label.font = ('<System>', 13)
        self.info_label.text = (
            f"推定回数: {analysis_result.get('count', 0)}回\n"
            f"青: 音量  赤破線: しきい値  縦点線: 検出ピーク"
        )
        self.add_subview(self.info_label)

        self.zoom_button = ui.Button(title='拡大表示')
        self.zoom_button.font = ('<System-Bold>', 18)
        self.zoom_button.background_color = '#d9ecff'
        self.zoom_button.corner_radius = 10
        self.zoom_button.action = self.show_large_image
        self.add_subview(self.zoom_button)

        self.scroll_zoom_button = ui.Button(title='横スクロールで拡大表示')
        self.scroll_zoom_button.font = ('<System-Bold>', 18)
        self.scroll_zoom_button.background_color = '#d8f0dd'
        self.scroll_zoom_button.corner_radius = 10
        self.scroll_zoom_button.action = self.show_wide_scroll_image
        self.add_subview(self.scroll_zoom_button)

        self.close_button = ui.Button(title='閉じる')
        self.close_button.font = ('<System-Bold>', 18)
        self.close_button.background_color = '#eeeeee'
        self.close_button.corner_radius = 10
        self.close_button.action = self.close_self
        self.add_subview(self.close_button)

        if self.image_path and os.path.exists(self.image_path):
            self.image_view.image = ui.Image.named(self.image_path)

    def layout(self):
        padding = 12
        top_h = 52
        button_h = 40
        button_gap = 8
        bottom_area_h = button_h * 3 + button_gap * 2 + 16

        self.info_label.frame = (padding, 8, self.width - padding * 2, 42)

        self.image_view.frame = (
            padding,
            top_h,
            self.width - padding * 2,
            self.height - top_h - bottom_area_h
        )

        y0 = self.height - bottom_area_h + 8

        self.zoom_button.frame = (
            padding, y0, self.width - padding * 2, button_h
        )
        self.scroll_zoom_button.frame = (
            padding, y0 + button_h + button_gap, self.width - padding * 2, button_h
        )
        self.close_button.frame = (
            padding,
            self.height - button_h - 8,
            self.width - padding * 2,
            button_h
        )

    def show_large_image(self, sender):
        if not self.image_path or not os.path.exists(self.image_path):
            dialogs.alert('拡大表示', '画像が見つかりません。', 'OK', hide_cancel_button=True)
            return

        large_view = LargeImageView(self.image_path)
        large_view.present('full_screen')

    def show_wide_scroll_image(self, sender):
        target_path = self.wide_image_path or self.image_path
        if not target_path or not os.path.exists(target_path):
            dialogs.alert('横スクロール拡大', '画像が見つかりません。', 'OK', hide_cancel_button=True)
            return

        wide_view = WideScrollableImageView(target_path)
        wide_view.present('full_screen')

    def close_self(self, sender):
        self.close()


# =========================
# 履歴画面
# =========================
class HistoryView(ui.View):
    def __init__(self, records):
        super().__init__()
        self.name = '測定履歴'
        self.background_color = 'white'
        self.records = records

        self.table = ui.TableView(frame=self.bounds, flex='WH')
        self.table.data_source = self
        self.table.delegate = self
        self.add_subview(self.table)

    def tableview_number_of_rows(self, tableview, section):
        return len(self.records)

    def tableview_cell_for_row(self, tableview, section, row):
        cell = ui.TableViewCell('subtitle')
        record = self.records[row]

        manual_count = record.get('count', 0)
        auto_count = record.get('estimated_count', '')

        cell.text_label.text = (
            f"{record.get('datetime', '')}  "
            f"{record.get('syllable', '')}  "
            f"手動:{manual_count}回"
        )

        detail = (
            f"{record.get('duration', '')}秒 / "
            f"1秒あたり {record.get('per_second', 0):.2f}回"
        )

        if auto_count != '':
            detail += f" / 自動:{auto_count}回"

        if record.get('audio_file_name'):
            detail += f" / 録音:{record['audio_file_name']}"

        cell.detail_text_label.text = detail
        return cell


# =========================
# メイン画面
# =========================
class KatsuzetsuApp(ui.View):
    def __init__(self):
        super().__init__()
        self.name = APP_TITLE
        self.background_color = 'white'

        self.selected_syllable = 'か'
        self.selected_duration = 5

        self.is_countdown = False
        self.is_measuring = False
        self.count = 0
        self.remaining = 0
        self.countdown_value = 0

        self.audio = AudioManager()
        self.last_audio_file_path = None
        self.last_analysis_result = None

        self.frame_ms = DEFAULT_FRAME_MS
        self.threshold_ratio = DEFAULT_THRESHOLD_RATIO
        self.min_gap_ms = DEFAULT_MIN_GAP_MS

        self.make_ui()
        self.update_labels()

    def make_ui(self):
        self.scroll_view = ui.ScrollView()
        self.scroll_view.flex = 'WH'
        self.scroll_view.always_bounce_vertical = True
        self.add_subview(self.scroll_view)

        self.content_view = ui.View()
        self.content_view.background_color = 'white'
        self.scroll_view.add_subview(self.content_view)

        self.guide_label = ui.Label()
        self.guide_label.text = '発音文字と時間を選んでください'
        self.guide_label.alignment = ui.ALIGN_CENTER
        self.guide_label.font = ('<System-Bold>', 20)
        self.content_view.add_subview(self.guide_label)

        self.syllable_label = ui.Label()
        self.syllable_label.text = '発音文字'
        self.syllable_label.font = ('<System-Bold>', 18)
        self.content_view.add_subview(self.syllable_label)

        self.syllable_button = ui.Button(title=self.selected_syllable)
        self.syllable_button.font = ('<System-Bold>', 24)
        self.syllable_button.background_color = '#d9ecff'
        self.syllable_button.corner_radius = 8
        self.syllable_button.action = self.select_syllable
        self.content_view.add_subview(self.syllable_button)

        self.duration_label = ui.Label()
        self.duration_label.text = '測定時間'
        self.duration_label.font = ('<System-Bold>', 18)
        self.content_view.add_subview(self.duration_label)

        self.duration_button = ui.Button(title=f'{self.selected_duration}秒')
        self.duration_button.font = ('<System-Bold>', 24)
        self.duration_button.background_color = '#d9ecff'
        self.duration_button.corner_radius = 8
        self.duration_button.action = self.select_duration
        self.content_view.add_subview(self.duration_button)

        self.timer_label = ui.Label()
        self.timer_label.text = '残り時間: -'
        self.timer_label.alignment = ui.ALIGN_CENTER
        self.timer_label.font = ('<System-Bold>', 22)
        self.content_view.add_subview(self.timer_label)

        self.countdown_label = ui.Label()
        self.countdown_label.text = ''
        self.countdown_label.alignment = ui.ALIGN_CENTER
        self.countdown_label.font = ('<System-Bold>', 56)
        self.countdown_label.text_color = '#0066cc'
        self.content_view.add_subview(self.countdown_label)

        self.count_label = ui.Label()
        self.count_label.text = '0'
        self.count_label.alignment = ui.ALIGN_CENTER
        self.count_label.font = ('<System-Bold>', 42)
        self.count_label.text_color = '#cc0000'
        self.content_view.add_subview(self.count_label)

        self.start_button = ui.Button(title='開始')
        self.start_button.font = ('<System-Bold>', 24)
        self.start_button.background_color = '#c8f7c5'
        self.start_button.corner_radius = 12
        self.start_button.action = self.start_countdown
        self.content_view.add_subview(self.start_button)

        self.count_button = ui.Button(title='発音したら押す')
        self.count_button.font = ('<System-Bold>', 28)
        self.count_button.background_color = '#ffd9b3'
        self.count_button.corner_radius = 18
        self.count_button.action = self.increment_count
        self.content_view.add_subview(self.count_button)

        self.reset_button = ui.Button(title='リセット')
        self.reset_button.font = ('<System-Bold>', 18)
        self.reset_button.background_color = '#eeeeee'
        self.reset_button.corner_radius = 10
        self.reset_button.action = self.reset_count
        self.content_view.add_subview(self.reset_button)

        self.history_button = ui.Button(title='履歴')
        self.history_button.font = ('<System-Bold>', 18)
        self.history_button.background_color = '#eeeeee'
        self.history_button.corner_radius = 10
        self.history_button.action = self.show_history
        self.content_view.add_subview(self.history_button)

        self.play_button = ui.Button(title='最後の録音を再生')
        self.play_button.font = ('<System-Bold>', 18)
        self.play_button.background_color = '#e0d8ff'
        self.play_button.corner_radius = 10
        self.play_button.action = self.play_last_audio
        self.content_view.add_subview(self.play_button)

        self.analyze_button = ui.Button(title='最後の録音を解析')
        self.analyze_button.font = ('<System-Bold>', 18)
        self.analyze_button.background_color = '#d8f0dd'
        self.analyze_button.corner_radius = 10
        self.analyze_button.action = self.analyze_last_audio
        self.content_view.add_subview(self.analyze_button)

        self.graph_button = ui.Button(title='最後の録音をグラフ表示')
        self.graph_button.font = ('<System-Bold>', 18)
        self.graph_button.background_color = '#ffe5b4'
        self.graph_button.corner_radius = 10
        self.graph_button.action = self.show_analysis_graph
        self.content_view.add_subview(self.graph_button)

        self.info_label = ui.Label()
        self.info_label.number_of_lines = 0
        self.info_label.alignment = ui.ALIGN_CENTER
        self.info_label.font = ('<System>', 14)
        self.info_label.text = (
            '解析グラフでは、青=音量、赤破線=しきい値、縦点線=検出ピークを表示します。'
        )
        self.content_view.add_subview(self.info_label)

    def layout(self):
        self.scroll_view.frame = self.bounds

        w = self.width
        content_w = w
        y = 6

        self.guide_label.frame = (20, y, content_w - 40, 28)
        y += 36

        self.syllable_label.frame = (20, y + 6, 100, 28)
        self.syllable_button.frame = (120, y, content_w - 140, 40)
        y += 52

        self.duration_label.frame = (20, y + 6, 100, 28)
        self.duration_button.frame = (120, y, content_w - 140, 40)
        y += 58

        self.timer_label.frame = (20, y, content_w - 40, 28)
        y += 32

        self.countdown_label.frame = (20, y, content_w - 40, 56)
        y += 58

        self.count_label.frame = (20, y, content_w - 40, 48)
        y += 56

        self.start_button.frame = (20, y, content_w - 40, 46)
        y += 56

        self.count_button.frame = (20, y, content_w - 40, 110)
        y += 122

        btn_w = (content_w - 60) / 2
        self.reset_button.frame = (20, y, btn_w, 40)
        self.history_button.frame = (40 + btn_w, y, btn_w, 40)
        y += 48

        self.play_button.frame = (20, y, content_w - 40, 40)
        y += 46

        self.analyze_button.frame = (20, y, content_w - 40, 40)
        y += 46

        self.graph_button.frame = (20, y, content_w - 40, 40)
        y += 46

        self.info_label.frame = (20, y, content_w - 40, 44)
        y += 54

        content_h = max(self.height + 1, y)
        self.content_view.frame = (0, 0, content_w, content_h)
        self.scroll_view.content_size = (content_w, content_h)

    def update_labels(self):
        self.syllable_button.title = self.selected_syllable
        self.duration_button.title = f'{self.selected_duration}秒'
        self.count_label.text = str(self.count)

        if self.is_countdown:
            self.timer_label.text = 'まもなく開始'
        elif self.is_measuring:
            self.timer_label.text = f'残り時間: {self.remaining}秒'
        else:
            self.timer_label.text = '残り時間: -'

    def set_controls_enabled(self, enabled):
        self.start_button.enabled = enabled
        self.syllable_button.enabled = enabled
        self.duration_button.enabled = enabled
        self.reset_button.enabled = enabled
        self.history_button.enabled = enabled
        self.play_button.enabled = enabled
        self.analyze_button.enabled = enabled
        self.graph_button.enabled = enabled

    def select_syllable(self, sender):
        if self.is_countdown or self.is_measuring:
            return
        result = dialogs.list_dialog('発音を選択', SYLLABLES)
        if result:
            self.selected_syllable = result
            self.update_labels()

    def select_duration(self, sender):
        if self.is_countdown or self.is_measuring:
            return
        choices = [f'{d}秒' for d in DURATIONS]
        result = dialogs.list_dialog('測定時間を選択', choices)
        if result:
            self.selected_duration = int(result.replace('秒', ''))
            self.update_labels()

    def reset_count(self, sender):
        if self.is_countdown or self.is_measuring:
            return
        self.count = 0
        self.countdown_label.text = ''
        self.last_analysis_result = None
        self.update_labels()

    def start_countdown(self, sender):
        if self.is_countdown or self.is_measuring:
            return

        self.audio.stop_playback()

        self.count = 0
        self.remaining = self.selected_duration
        self.countdown_value = 3
        self.is_countdown = True
        self.last_analysis_result = None

        self.set_controls_enabled(False)
        self.count_button.enabled = False

        self.show_countdown_step()

    def show_countdown_step(self):
        if not self.is_countdown:
            return

        if self.countdown_value > 0:
            self.countdown_label.text = str(self.countdown_value)
            self.update_labels()
            self.countdown_value -= 1
            ui.delay(self.show_countdown_step, 1.0)
        else:
            self.countdown_label.text = 'スタート'
            self.update_labels()
            ui.delay(self.begin_measurement, 0.8)

    def begin_measurement(self):
        self.is_countdown = False
        self.is_measuring = True
        self.countdown_label.text = ''

        ok, message = self.audio.start_recording(self.selected_syllable)
        if ok:
            self.last_audio_file_path = message
        else:
            self.last_audio_file_path = None
            dialogs.alert('録音開始エラー', message, 'OK', hide_cancel_button=True)

        self.count_button.enabled = True
        self.update_labels()
        ui.delay(self.tick, 1.0)

    def tick(self):
        if not self.is_measuring:
            return

        self.remaining -= 1
        self.update_labels()

        if self.remaining <= 0:
            self.finish_measurement()
        else:
            ui.delay(self.tick, 1.0)

    def increment_count(self, sender):
        if not self.is_measuring:
            return
        self.count += 1
        self.update_labels()

    def finish_measurement(self):
        self.is_measuring = False
        self.audio.stop_recording()

        self.set_controls_enabled(True)
        self.count_button.enabled = True
        self.timer_label.text = '測定終了'

        per_second = self.count / self.selected_duration
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        audio_file_name = ''
        if self.last_audio_file_path:
            audio_file_name = os.path.basename(self.last_audio_file_path)

        record = {
            'datetime': now_str,
            'syllable': self.selected_syllable,
            'duration': self.selected_duration,
            'count': self.count,
            'per_second': per_second,
            'audio_file_path': self.last_audio_file_path or '',
            'audio_file_name': audio_file_name,
            'estimated_count': '',
        }

        records = load_records()
        records.insert(0, record)
        save_records(records)

        dialogs.alert(
            '結果',
            f"発音: {self.selected_syllable}\n"
            f"手動回数: {self.count}回\n"
            f"1秒あたり: {per_second:.2f}回\n"
            f"録音: {'あり' if self.last_audio_file_path else 'なし'}",
            'OK',
            hide_cancel_button=True
        )

    def play_last_audio(self, sender):
        if self.is_countdown or self.is_measuring:
            return

        if not self.last_audio_file_path:
            dialogs.alert('再生', 'まだ録音がありません。', 'OK', hide_cancel_button=True)
            return

        ok, message = self.audio.play_file(self.last_audio_file_path)
        if not ok:
            dialogs.alert('再生エラー', message, 'OK', hide_cancel_button=True)

    def analyze_last_audio(self, sender):
        if self.is_countdown or self.is_measuring:
            return

        if not self.last_audio_file_path:
            dialogs.alert('解析', 'まだ録音がありません。', 'OK', hide_cancel_button=True)
            return

        result = estimate_syllable_count_from_wav(
            self.last_audio_file_path,
            frame_ms=self.frame_ms,
            threshold_ratio=self.threshold_ratio,
            min_gap_ms=self.min_gap_ms
        )

        self.last_analysis_result = result

        peak_text = ''
        if result['peak_times']:
            peak_text = '\n検出時刻(秒):\n' + ', '.join(
                f'{t:.2f}' for t in result['peak_times']
            )

        records = load_records()
        if records:
            latest = records[0]
            if latest.get('audio_file_path', '') == (self.last_audio_file_path or ''):
                latest['estimated_count'] = result['count']
                save_records(records)

        dialogs.alert(
            '解析結果',
            result['message'] + peak_text,
            'OK',
            hide_cancel_button=True
        )

    def show_analysis_graph(self, sender):
        if self.is_countdown or self.is_measuring:
            return

        if not self.last_audio_file_path:
            dialogs.alert('グラフ', 'まだ録音がありません。', 'OK', hide_cancel_button=True)
            return

        result = estimate_syllable_count_from_wav(
            self.last_audio_file_path,
            frame_ms=self.frame_ms,
            threshold_ratio=self.threshold_ratio,
            min_gap_ms=self.min_gap_ms
        )

        self.last_analysis_result = result

        records = load_records()
        if records:
            latest = records[0]
            if latest.get('audio_file_path', '') == (self.last_audio_file_path or ''):
                latest['estimated_count'] = result['count']
                save_records(records)

        image_path = create_matplotlib_graph_image(result, wide=False)
        if not image_path:
            dialogs.alert('グラフ', 'グラフ画像を作成できませんでした。', 'OK', hide_cancel_button=True)
            return

        wide_image_path = create_matplotlib_graph_image(result, wide=True)

        graph_view = AnalysisGraphView(image_path, result, wide_image_path=wide_image_path)
        graph_view.present('full_screen')

    def show_history(self, sender):
        records = load_records()
        if not records:
            dialogs.alert('履歴', 'まだ記録がありません。', 'OK', hide_cancel_button=True)
            return

        history_view = HistoryView(records)
        history_view.present('sheet')


# =========================
# 実行
# =========================
if __name__ == '__main__':
    view = KatsuzetsuApp()
    view.present('fullscreen')
