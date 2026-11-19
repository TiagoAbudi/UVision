export type Page = 'monitor' | 'events' | 'devices' | 'calibration' | 'alerts' | 'performance' | 'about'
export type Camera = { id:number; name:string; online:boolean; host:string; fps:number; analysisFps:number; frameAgeSeconds:number|null; resultAgeMs:number; reconnectCount:number; personCount:number; ptz:boolean; width:number; height:number; connectionState:string; connectionMessage:string; fall:{enabled?:boolean; state?:string; perspectiveGuard?:boolean; shadowMode?:boolean; calibrationRequired?:boolean} }
export type Clip = {file:string;channel:number;date:string;size:number;duration:number;review:'fall'|'normal'|'unsure'|null;protected:boolean;thumbnail:boolean}
export type Player = {file:string|null;playing:boolean;position:number;duration:number;speed:number;fps:number;frame:number}
export type Telegram = {enabled:boolean;configured:boolean;chats:string[];channels:number[];allowCalibration:boolean;reminderSeconds:number;maxReminders:number;message:string}
export type Alert = {id:string;channel:number;cameraName:string;createdAt:string;status:string;shadow:boolean;clip:string|null;acknowledgedAt:string|null;delivered:number;failed:boolean}
export type Layout = {mode:string;order:number[];collapsed:boolean}
export type Profile = {image_size:number;interval:number;analysis_max_side:number}
export type ProfileChoice=Profile&{id:string;name:string}
export type State = {ready:boolean;error:string;cameras:Camera[];layout:Layout;ai:{ready?:boolean;device?:string;model?:string;averageInferenceMs?:number;p95InferenceMs?:number;inferenceMs?:number;analyzedCameras?:number;error?:string;warmingUp?:boolean};history:{time:number;latency:number;fps:number}[];alerts:Alert[];telegram:Telegram|null;player:Player;profile:Profile|null;profileChoices?:ProfileChoice[];recovery:string;environment:{version:string;python:string;backend:string;startedAt:string|null}}
type Signal = {connect:(callback:(...args:string[])=>void)=>void}
type Native = {request:(id:string, action:string, payload:string)=>void;response:Signal;stateChanged:Signal}
declare global {interface Window {qt?:{webChannelTransport:unknown};QWebChannel:new(transport:unknown, callback:(channel:{objects:{vision:Native}})=>void)=>unknown}}
const listeners = new Set<(state:State)=>void>()
const pending = new Map<string,{resolve:(value:unknown)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>()
let sequence=0
const session=`${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`
const native = new Promise<Native>((resolve,reject)=>{
 if(!window.qt || !window.QWebChannel){reject(new Error('Abra a interface pelo aplicativo UStudio Vision.'));return}
 new window.QWebChannel(window.qt.webChannelTransport, channel=>{
  const bridge=channel.objects.vision
  bridge.response.connect((id,raw)=>{const item=pending.get(id);if(!item)return;clearTimeout(item.timer);pending.delete(id);const message=JSON.parse(raw);if(message.error)item.reject(new Error(message.error));else item.resolve(message.data)})
  bridge.stateChanged.connect(raw=>{const state:State=JSON.parse(raw);listeners.forEach(listener=>listener(state))})
  resolve(bridge)
 })
})
export async function call<T=boolean>(action:string,payload:object={}):Promise<T>{
 const bridge=await native;const id=`${session}:${++sequence}`
 return new Promise<T>((resolve,reject)=>{pending.set(id,{resolve: value=>resolve(value as T),reject,timer:setTimeout(()=>{pending.delete(id);reject(new Error('A operação está demorando. Confira o estado antes de repetir.'))},45000)});bridge.request(id,action,JSON.stringify(payload))})
}
export function subscribe(listener:(state:State)=>void){listeners.add(listener);return()=>{listeners.delete(listener)}}
export const imageUrl=(key:string)=>`uvision://app/image/${key.split('/').map(encodeURIComponent).join('/')}`
export const cameraName=(camera:Camera)=>camera.id===4?'Cozinha':camera.name
export const cameraCode=(id:number)=>`CH ${String(id).padStart(2,'0')}`
export const dateTime=(value:string)=>new Date(value).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'})
export const duration=(seconds:number)=>`${Math.floor(seconds/60).toString().padStart(2,'0')}:${Math.floor(seconds%60).toString().padStart(2,'0')}`
