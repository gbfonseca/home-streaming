# Guia de Configuração da Stack de Mídia (Servarr + qBittorrent + FlareSolverr + Jellyfin)

Este repositório contém o ambiente automatizado para download, organização, contorno de proteção Cloudflare e legendagem de filmes e séries utilizando **Docker Compose** no **WSL2**, integrado ao **Jellyfin** (instalado diretamente no sistema operacional).

---

## 🧭 Visão Geral e Endereços de Acesso

| Serviço | Porta Host | URL no Navegador | URL Interna (Comunicação Docker) | Finalidade |
| :--- | :---: | :--- | :--- | :--- |
| **qBittorrent** | `8080` | `http://localhost:8080` | `http://qbittorrent:8080` | Cliente de Torrent |
| **Prowlarr** | `9696` | `http://localhost:9696` | `http://prowlarr:9696` | Gerenciador de Indexadores |
| **FlareSolverr** | `8191` | `http://localhost:8191` | `http://flaresolverr:8191` | Bypass de proteção Cloudflare |
| **Radarr** | `7878` | `http://localhost:7878` | `http://radarr:7878` | Gerenciador de Filmes |
| **Sonarr** | `8989` | `http://localhost:8989` | `http://sonarr:8989` | Gerenciador de Séries |
| **Bazarr** | `6767` | `http://localhost:6767` | `http://bazarr:6767` | Gerenciador de Legendas |
| **Jellyfin** | `8096` | `http://localhost:8096` | `http://host.docker.internal:8096` | Servidor de Mídia (no Host) |

---

## 📁 Mapeamento de Volumes (Windows ↔ WSL ↔ Containers)

Todos os dados de mídia e downloads ficam centralizados no disco `E:\Multimídia`:

| Origem no Windows | Caminho no WSL | Destino nos Containers | Finalidade |
| :--- | :--- | :--- | :--- |
| `E:\Multimídia\Downloads` | `/mnt/e/Multimídia/Downloads` | `/downloads` *(qBittorrent, Radarr, Sonarr)* | Downloads brutos (em progresso/seeding) |
| `E:\Multimídia\Filmes` | `/mnt/e/Multimídia/Filmes` | `/movies` *(Radarr, Bazarr)* | Filmes renomeados e organizados |
| `E:\Multimídia\Series` | `/mnt/e/Multimídia/Series` | `/tv` *(Sonarr, Bazarr)* | Séries renomeadas e organizadas |

---

## 🚀 Inicialização

Para iniciar todos os containers em segundo plano:

```bash
docker compose up -d
```

Para verificar os logs (ou a senha inicial do qBittorrent):
```bash
docker compose logs -f qbittorrent
```

---

## ⚙️ Passo a Passo de Configuração

### 1. qBittorrent (Cliente de Download)
1. Acesse `http://localhost:8080`.
2. O usuário padrão é `admin`. A senha inicial temporária estará nos logs do container (`docker compose logs qbittorrent`).
3. Vá em **Opções (Engrenagem)** > **Web UI** e defina seu usuário e senha definitivos.
4. Em **Downloads**, certifique-se de que a pasta de download é `/downloads`.

---

### 2. Prowlarr & FlareSolverr (Indexadores e Bypass de Cloudflare)
1. Acesse `http://localhost:9696`.
2. **Configurar o Proxy FlareSolverr (para evitar bloqueios)**:
   - Vá em **Settings** > **Indexers**.
   - Na seção **Proxy**, clique no botão **`+`** e selecione **FlareSolverr**.
   - *Name*: `FlareSolverr`
   - *Host*: `http://flaresolverr:8191`
   - Clique em **Test** e depois em **Save**.
3. **Adicionar Indexadores**:
   - Vá em **Indexers** > clique no botão **`+`**.
   - Adicione seus indexadores favoritos (ex.: 1337x, YTS, Torrentio, etc.). Os indexadores protegidos por Cloudflare utilizarão o FlareSolverr automaticamente.
4. **Sincronizar com Radarr e Sonarr**:
   - Vá em **Settings** > **Apps** > clique em **`+`**:
     - **Radarr**:
       - *Prowlarr Server*: `http://prowlarr:9696`
       - *Radarr Server*: `http://radarr:7878`
       - *API Key*: Obtenha em *Radarr > Settings > General*.
     - **Sonarr**:
       - *Prowlarr Server*: `http://prowlarr:9696`
       - *Sonarr Server*: `http://sonarr:8989`
       - *API Key*: Obtenha em *Sonarr > Settings > General*.

---

### 3. Radarr (Filmes)
1. Acesse `http://localhost:7878`.
2. **Pasta Raiz (Root Folder)**:
   - Em **Settings** > **Media Management** > **Root Folders**, adicione `/movies`.
3. **Download Client**:
   - Em **Settings** > **Download Clients** > clique em **`+`** > selecione **qBittorrent**.
   - *Host*: `qbittorrent`
   - *Port*: `8080`
   - *Username* / *Password*: Suas credenciais do qBittorrent.
4. **API Key**:
   - Copie em **Settings** > **General** para usar no Prowlarr e Bazarr.

---

### 4. Sonarr (Séries / Animes)
1. Acesse `http://localhost:8989`.
2. **Pasta Raiz (Root Folder)**:
   - Em **Settings** > **Media Management** > **Root Folders**, adicione `/tv`.
3. **Download Client**:
   - Em **Settings** > **Download Clients** > clique em **`+`** > selecione **qBittorrent**.
   - *Host*: `qbittorrent`
   - *Port*: `8080`
   - *Username* / *Password*: Suas credenciais do qBittorrent.
4. **API Key**:
   - Copie em **Settings** > **General** para usar no Prowlarr e Bazarr.

---

### 5. Bazarr (Legendas)
1. Acesse `http://localhost:6767`.
2. **Conectar ao Sonarr**:
   - Em **Settings** > **Sonarr** > marque **Enabled** > *Address*: `sonarr` | *Port*: `8989` | *API Key*: Chave do Sonarr.
3. **Conectar ao Radarr**:
   - Em **Settings** > **Radarr** > marque **Enabled** > *Address*: `radarr` | *Port*: `7878` | *API Key*: Chave do Radarr.
4. **Provedores e Idiomas**:
   - Configure provedores em **Providers** (ex.: *OpenSubtitles.com*, *Subdl*) e defina o idioma padrão em **Languages** (ex.: `pt-BR`).

---

### 6. Jellyfin (Instalado no SO / Windows)
1. Acesse `http://localhost:8096`.
2. Em **Dashboard** > **Libraries**:
   - **Biblioteca de Filmes**: Aponte para `E:\Multimídia\Filmes`.
   - **Biblioteca de Séries**: Aponte para `E:\Multimídia\Series`.
