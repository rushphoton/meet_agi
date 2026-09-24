"use client";
/*
 * WHY THIS EXISTS
 * The settings screen: which documents the bot reads, how raw meeting names
 * map to the names and roles shown in the transcript, and which AI model does
 * which job. Everything else in the backend's settings is kept as it is when
 * you save.
 *
 * FAILURE IT PREVENTS
 * Having to edit files by hand before a demo to fix a speaker's name or swap
 * a model.
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { DocumentInfo, Settings, SpeakerMapping } from "@/lib/contract";
import { formatDate } from "@/lib/format";

const MODEL_JOBS = [
  ["cheap_check", "Cheap \"worth a closer look\" check"],
  ["judge", "Dispute judgement"],
  ["answer", "Spoken answer"],
  ["summary", "Summary + follow-ups"],
] as const;

const msg = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const loadDocs = () => api.listDocuments().then(setDocs).catch((e) => setError(msg(e)));

  useEffect(() => {
    api.getSettings().then(setSettings).catch((e) => setError(msg(e)));
    void loadDocs();
  }, []);

  function setSpeaker(i: number, patch: Partial<SpeakerMapping>) {
    if (!settings) return;
    const speakers = settings.speakers.map((s, j) => (j === i ? { ...s, ...patch } : s));
    setSettings({ ...settings, speakers });
  }

  async function save() {
    if (!settings) return;
    setError(null);
    setSaved(null);
    try {
      const clean = { ...settings, speakers: settings.speakers.filter((s) => s.match_name.trim() && s.display_name.trim()) };
      setSettings(await api.putSettings(clean));
      setSaved("Saved.");
    } catch (e) {
      setError(msg(e));
    }
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    setError(null);
    try {
      await api.uploadDocument(file);
      await loadDocs();
    } catch (e) {
      setError(msg(e));
    }
  }

  async function remove(name: string) {
    if (!confirm(`Delete ${name} from the bot's documents?`)) return;
    try {
      await api.deleteDocument(name);
      await loadDocs();
    } catch (e) {
      setError(msg(e));
    }
  }

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">Documents, speaker names and models. Changes apply to the next sentence the bot hears.</p>
      {error && <div className="error">{error}</div>}

      <section className="panel">
        <h2>Documents</h2>
        <p className="small muted">.md, .txt or .pdf. Files whose names start with SAMPLE_ are demo placeholders.</p>
        <table>
          <thead><tr><th>Name</th><th className="num">Size</th><th className="num">Passages</th><th>Added</th><th /></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.name}>
                <td>{d.name}{d.name.startsWith("SAMPLE_") && <span className="badge canned">SAMPLE</span>}</td>
                <td className="num">{Math.ceil(d.size_bytes / 1024)} KB</td>
                <td className="num">{d.chunks}</td>
                <td>{formatDate(d.added_at)}</td>
                <td><button className="danger" onClick={() => void remove(d.name)}>Delete</button></td>
              </tr>
            ))}
            {docs.length === 0 && <tr><td colSpan={5} className="muted">No documents yet.</td></tr>}
          </tbody>
        </table>
        <p><input type="file" accept=".md,.txt,.pdf" onChange={(e) => void upload(e.target.files?.[0])} /></p>
      </section>

      {settings && (
        <>
          <section className="panel">
            <h2>Speaker names</h2>
            <p className="small muted">When the meeting shows the name on the left, the transcript shows the name on the right.</p>
            <table>
              <thead><tr><th>Name in the meeting</th><th>Show as</th><th>Role</th><th /></tr></thead>
              <tbody>
                {settings.speakers.map((s, i) => (
                  <tr key={i}>
                    <td><input type="text" value={s.match_name} onChange={(e) => setSpeaker(i, { match_name: e.target.value })} /></td>
                    <td><input type="text" value={s.display_name} onChange={(e) => setSpeaker(i, { display_name: e.target.value })} /></td>
                    <td><input type="text" value={s.role ?? ""} onChange={(e) => setSpeaker(i, { role: e.target.value || null })} /></td>
                    <td><button onClick={() => setSettings({ ...settings, speakers: settings.speakers.filter((_, j) => j !== i) })}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              <button onClick={() => setSettings({ ...settings, speakers: [...settings.speakers, { match_name: "", display_name: "", role: null }] })}>
                Add speaker
              </button>
            </p>
          </section>

          <section className="panel">
            <h2>Models</h2>
            <table>
              <tbody>
                {MODEL_JOBS.map(([key, label]) => (
                  <tr key={key}>
                    <td>{label}</td>
                    <td>
                      <input type="text" value={settings.models[key]}
                        onChange={(e) => setSettings({ ...settings, models: { ...settings.models, [key]: e.target.value } })} />
                    </td>
                  </tr>
                ))}
                <tr>
                  <td>Voice ({settings.voice.provider}, {settings.voice.model})</td>
                  <td>
                    <input type="text" value={settings.voice.voice_id}
                      onChange={(e) => setSettings({ ...settings, voice: { ...settings.voice, voice_id: e.target.value } })} />
                  </td>
                </tr>
              </tbody>
            </table>
          </section>

          <div className="controls">
            <button className="primary" onClick={() => void save()}>Save settings</button>
            {saved && <span className="badge ok">{saved}</span>}
          </div>
        </>
      )}
    </>
  );
}
