# Dark Colony Server

Standalone multiplayer server for the classic game *Dark Colony* (1997).  
Reverse-engineered the original built-in TCP/IP server for interoperability and online play.

---

## Disclaimer

This project is a fan-made, open-source recreation of the original *Dark Colony* network server.  
It was developed through **reverse engineering** for the sole purpose of **interoperability** and **preservation** of the original multiplayer experience.  

This project **does not include or distribute any original game assets, binaries, or copyrighted material** from *Dark Colony* (1997).  
You must own a legitimate copy of the game to use this software.

All trademarks and copyrights are the property of their respective owners.

---

## Reverse Engineering Methodology
- To ensure full legal compliance and avoid copyright infringement, we employed a "Clean Room Design" methodology:
- Network Traffic Analysis: The game's network protocol was recreated from scratch based exclusively on the analysis of captured network packets using the Wireshark software.
- Functional Ideas, Not Code: We analyzed the functional behavior of the protocol, not the original game's source code. The game's executable files were not decompiled or disassembled.

---

## Features / Goals
- Public internet server for players worldwide  
- Unlimited rooms (each up to 8 players)  
- Tournaments  
- Admin commands (switch rooms, maps, options)

---

## How to Run
1. Install [Node.js](https://nodejs.org)  
2. Download or clone this repo  
3. Open a terminal in the project folder  
4. Run:
   ```bash
   node ./server.js
   ```
5. Keep the terminal open while it runs  
6. Launch *Dark Colony* → **MULTI PLAYER WAR** → **CONNECT TO SERVER**  
7. Enter `localhost` as the IP address

---

## License
Licensed under the **GNU Affero General Public License v3 (AGPLv3)**.  
You can use, modify, and share this project — **as long as your version stays open source** under the same license.  

[Read full license →](./LICENSE)

---

(c) 2025 Nikolajs Agafonovs
