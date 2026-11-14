'use strict';
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick',event=>{event.notification.close();event.waitUntil(self.clients.matchAll({type:'window'}).then(windows=>{for(const client of windows){if(new URL(client.url).origin===self.location.origin)return client.focus();}return self.clients.openWindow('/');}));});
