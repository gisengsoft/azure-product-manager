import streamlit as st
import os
import pyodbc
import pandas as pd
from azure.storage.blob import BlobServiceClient
import uuid
from dotenv import load_dotenv
import time
import re
from PIL import Image
import io
from datetime import datetime
import random

# Desabilitar pooling para ajudar a resolver problemas de conexão
pyodbc.pooling = False

# Configurações básicas
try:
    st.set_page_config(page_title="Cadastro de Produtos", layout="wide")
    
    # Adicionar CSS personalizado para melhorar a aparência dos cards
    st.markdown("""
    <style>
        /* Estilo para container de produto */
        .stButton button {
            width: 100%;
        }
        
        /* Área do produto */
        div[data-testid="column"] > div:nth-child(1) {
            background-color: #f9f9f9;
            border-radius: 10px;
            padding: 1rem;
            border: 1px solid #e0e0e0;
            height: 100%;
            transition: transform 0.3s;
        }
        
        /* Efeito hover nos cards */
        div[data-testid="column"] > div:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 20px rgba(0,0,0,0.1);
        }
        
        /* Espaçamento e alinhamento */
        div[data-testid="column"] img {
            display: block;
            margin: 0 auto;
            border-radius: 5px;
        }
        
        /* Tamanho máximo das imagens */
        .stImage img {
            max-height: 200px;
            object-fit: contain;
        }
        
        /* Estilo de preço */
        div[data-testid="column"] h3 {
            margin-top: 10px;
            margin-bottom: 5px;
        }
    </style>
    """, unsafe_allow_html=True)
    
except Exception as e:
    st.error(f"Erro na configuração da página: {str(e)}")

# Carrega variáveis de ambiente
load_dotenv()

# Configurações do Azure Blob Storage
blobConnectionString = os.getenv('BLOB_CONNECTION_STRING', "SUACHAVEAZURESTORAGE")
blobContainerName = os.getenv('BLOB_CONTAINER_NAME', "nomedoseuconteiner")
blobAccountName = os.getenv('BLOB_ACCOUNT_NAME', "nomedasuacontablob")

# Configurações SQL Server
SQL_SERVER = os.getenv('SQL_SERVER', "seuname.database.windows.net")
SQL_DATABASE = os.getenv('SQL_DATABASE', "seubanco")
SQL_USER = os.getenv('SQL_USER', "seuusuario")
SQL_PASSWORD = os.getenv('SQL_PASSWORD', "suasenha")

# Inicialização do estado da aplicação
if 'mensagem_sucesso' not in st.session_state:
    st.session_state.mensagem_sucesso = None

if 'mensagem_erro' not in st.session_state:
    st.session_state.mensagem_erro = None

if 'pagina_atual' not in st.session_state:
    st.session_state.pagina_atual = 1

# Novo sistema de estado simplificado
if 'produtos_enviados' not in st.session_state:
    st.session_state.produtos_enviados = set()

if 'processando_upload' not in st.session_state:
    st.session_state.processando_upload = False

if 'ultimo_upload_timestamp' not in st.session_state:
    st.session_state.ultimo_upload_timestamp = 0

# Variáveis para controle de exclusão
if 'produto_a_excluir' not in st.session_state:
    st.session_state.produto_a_excluir = None

if 'exclusao_confirmada' not in st.session_state:
    st.session_state.exclusao_confirmada = False

if 'exclusao_em_andamento' not in st.session_state:
    st.session_state.exclusao_em_andamento = False

# Criação do container de logs
log_container = st.sidebar.expander("Logs de Execução", expanded=False)

def log_debug(message, level="INFO"):
    """Função para registrar mensagens de log"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "SUCCESS": "✅"
    }.get(level, "ℹ️")
    
    with log_container:
        st.write(f"{timestamp} - {prefix} {message}")

# FUNÇÃO REVISADA: Limpar cache para resolver problemas de estado
def limpar_cache_produtos():
    """Remove todas as chaves relacionadas ao cache de produtos"""
    # Identificar chaves a remover
    chaves_para_remover = []
    for key in st.session_state:
        if key.startswith("confirmar_del_") or key.startswith("produto_") or key.startswith("del_"):
            chaves_para_remover.append(key)
    
    # Remover chaves identificadas
    for key in chaves_para_remover:
        if key in st.session_state:
            del st.session_state[key]
    
    # Resetar estados relacionados a produtos
    st.session_state.produtos_enviados = set()
    st.session_state.processando_upload = False
    st.session_state.ultimo_upload_timestamp = 0
    st.session_state.produto_a_excluir = None
    st.session_state.exclusao_confirmada = False
    st.session_state.exclusao_em_andamento = False
    
    # Outras variáveis de estado que podem precisar ser resetadas
    if 'form_key' in st.session_state:
        del st.session_state.form_key
    
    log_debug("Cache de produtos limpo com sucesso", "SUCCESS")

# Conexão SQL com tratamento de exceções e retentativas MELHORADA
def get_connection(max_retries=5, retry_delay=3):
    """
    Estabelece conexão com o banco de dados com mecanismo de retentativa melhorado
    """
    retries = 0
    last_exception = None
    
    while retries < max_retries:
        try:
            log_debug(f"Tentando conectar ao banco de dados: {SQL_SERVER}/{SQL_DATABASE} (tentativa {retries+1}/{max_retries})")
            
            # String de conexão com parâmetros otimizados
            conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={SQL_SERVER};"
                f"DATABASE={SQL_DATABASE};"
                f"UID={SQL_USER};"
                f"PWD={SQL_PASSWORD};"
                f"Connection Timeout=60;"
                f"Query Timeout=60;"
                f"Encrypt=yes;"
                f"TrustServerCertificate=yes;"
                f"ApplicationIntent=ReadWrite;"
                f"ConnectRetryCount=3;"
                f"ConnectRetryInterval=10;"
                f"MultipleActiveResultSets=True"
            )
            
            # Criar conexão com timeout explícito
            conn = pyodbc.connect(conn_str, timeout=60, autocommit=False)
            
            # Verificar se a conexão está realmente funcionando
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            
            log_debug("Conexão SQL estabelecida com sucesso", "SUCCESS")
            return conn
            
        except Exception as e:
            last_exception = e
            retries += 1
            log_debug(f"Falha na tentativa {retries} de conexão: {str(e)}", "WARNING")
            
            if retries < max_retries:
                wait_time = retry_delay * (2 ** (retries - 1))  # Backoff exponencial
                log_debug(f"Aguardando {wait_time:.2f}s antes da próxima tentativa...", "INFO")
                time.sleep(wait_time)
    
    log_debug(f"ERRO SQL: Todas as tentativas falharam. Último erro: {str(last_exception)}", "ERROR")
    return None

# Função para verificar a conexão do banco
def verificar_conexao():
    try:
        conn = get_connection(max_retries=2)
        if conn:
            conn.close()
            return True, "Conexão com o banco de dados estabelecida com sucesso!"
        else:
            return False, "Não foi possível estabelecer conexão com o banco de dados."
    except Exception as e:
        return False, f"Erro ao verificar conexão: {str(e)}"

# Inicializar banco de dados
def inicializar_banco_de_dados():
    try:
        conn = get_connection()
        if not conn:
            log_debug("Falha ao obter conexão com o banco de dados", "ERROR")
            return False
        
        cursor = conn.cursor()
        
        # Criar tabela Produtos se não existir
        cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Produtos')
        BEGIN
            CREATE TABLE Produtos (
                id INT IDENTITY(1,1) PRIMARY KEY,
                nome NVARCHAR(255) NOT NULL,
                descricao NVARCHAR(MAX),
                preco DECIMAL(10, 2) NOT NULL,
                imagem_url NVARCHAR(1000),
                imagem_blob_name NVARCHAR(500),
                categoria_id INT DEFAULT 1,
                estoque INT DEFAULT 0,
                data_criacao DATETIME DEFAULT GETDATE(),
                data_atualizacao DATETIME DEFAULT GETDATE()
            )
        END
        """)
        
        # Verificar se a coluna imagem_blob_name existe, se não, criar
        cursor.execute("""
        IF NOT EXISTS (
            SELECT * FROM sys.columns
            WHERE name = 'imagem_blob_name' AND object_id = OBJECT_ID('Produtos')
        )
        BEGIN
            ALTER TABLE Produtos ADD imagem_blob_name NVARCHAR(500) NULL
        END
        """)
        
        # Criar tabela Categorias se não existir
        cursor.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Categorias')
        BEGIN
            CREATE TABLE Categorias (
                id INT IDENTITY(1,1) PRIMARY KEY,
                nome NVARCHAR(255) NOT NULL,
                descricao NVARCHAR(MAX),
                data_criacao DATETIME DEFAULT GETDATE()
            );
            
            -- Inserir categoria padrão
            INSERT INTO Categorias (nome, descricao) VALUES ('Geral', 'Categoria geral de produtos');
        END
        """)
        
        conn.commit()
        conn.close()
        log_debug("Banco de dados inicializado com sucesso", "SUCCESS")
        return True
    except Exception as e:
        log_debug(f"Erro ao inicializar banco de dados: {str(e)}", "ERROR")
        return False

# Função para extrair o nome do blob da URL
def extrair_nome_blob_da_url(url):
    """Extrai o nome do blob a partir da URL completa"""
    try:
        # Padrão de URL: https://account.blob.core.windows.net/container/blobname
        parts = url.split('/')
        if len(parts) >= 4:
            return parts[-1]  # Último elemento é o nome do blob
        return None
    except Exception as e:
        log_debug(f"Erro ao extrair nome do blob da URL: {str(e)}", "ERROR")
        return None

# Função para sanitizar nomes de arquivo
def sanitizar_nome_arquivo(filename):
    """Remove caracteres inválidos do nome do arquivo"""
    nome_base, extensao = os.path.splitext(filename)
    nome_sanitizado = re.sub(r'[^a-zA-Z0-9._-]', '', nome_base.lower())
    
    if len(nome_sanitizado) > 50:
        nome_sanitizado = nome_sanitizado[:50]
    
    return f"{nome_sanitizado}{extensao.lower()}"

# Função para redimensionar imagem mantendo proporção
def redimensionar_imagem(image, max_dimension=800):
    """Redimensiona a imagem mantendo a proporção de aspecto"""
    width, height = image.size
    
    # Se a imagem já é menor que as dimensões máximas, retornar sem alteração
    if width <= max_dimension and height <= max_dimension:
        return image
    
    # Calcular novas dimensões mantendo a proporção
    if width > height:
        new_width = max_dimension
        new_height = int(height * (max_dimension / width))
    else:
        new_height = max_dimension
        new_width = int(width * (max_dimension / height))
    
    log_debug(f"Redimensionando imagem de {width}x{height} para {new_width}x{new_height}")
    return image.resize((new_width, new_height), Image.LANCZOS)

# FUNÇÃO CORRIGIDA: Comprimir imagem com compressão progressiva
def comprimir_imagem(file, max_size_kb=1024, initial_quality=85):
    """
    Comprime a imagem progressivamente até atingir o tamanho desejado ou qualidade mínima
    """
    try:
        if file is None:
            log_debug("Arquivo inválido para compressão", "ERROR")
            return None, None
            
        log_debug(f"Processando imagem: {file.name}")
        
        # Verifique se o arquivo tem conteúdo
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size == 0:
            log_debug("Arquivo vazio recebido", "ERROR")
            return None, None
            
        try:
            image = Image.open(file)
            
            # Obter largura e altura originais
            width, height = image.size
            log_debug(f"Dimensões originais: {width}x{height} pixels")
            
            # Converter para RGB se necessário
            if image.mode == 'RGBA':
                log_debug("Convertendo imagem RGBA para RGB")
                image = image.convert('RGB')
            
            # Detectar formato
            formato = file.name.split('.')[-1].upper()
            if formato not in ['JPEG', 'JPG', 'PNG']:
                formato = 'JPEG'
                
            # Redimensionar imagem se muito grande
            image = redimensionar_imagem(image, max_dimension=800)
            
            # Se já é menor que o tamanho máximo, retornar como está
            if file_size <= max_size_kb * 1024:
                log_debug("Imagem já está dentro do tamanho desejado")
                buffer = io.BytesIO()
                image.save(buffer, format=formato)
                buffer.seek(0)
                return buffer, formato.lower()
                
            # Compressão progressiva
            quality = initial_quality
            min_quality = 50  # Não vamos abaixo disso para manter qualidade mínima
            
            while quality >= min_quality:
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format=formato, optimize=True, quality=quality)
                tamanho_comprimido = len(img_byte_arr.getvalue())
                
                log_debug(f"Tentativa com qualidade {quality}: {tamanho_comprimido/1024:.2f} KB")
                
                # Se atingimos o tamanho desejado ou chegamos na qualidade mínima
                if tamanho_comprimido <= max_size_kb * 1024 or quality == min_quality:
                    log_debug(f"Tamanho final: {tamanho_comprimido/1024:.2f} KB (qualidade {quality})")
                    img_byte_arr.seek(0)
                    return img_byte_arr, formato.lower()
                
                # Reduzir qualidade e tentar novamente
                quality -= 10
            
            # Caso não consiga comprimir adequadamente, retornar a versão mais comprimida
            log_debug("Não foi possível comprimir a imagem para o tamanho desejado")
            img_byte_arr.seek(0)
            return img_byte_arr, formato.lower()
            
        except Exception as e:
            log_debug(f"Erro ao processar imagem: {str(e)}", "ERROR")
            return None, None
            
    except Exception as e:
        log_debug(f"Erro na compressão de imagem: {str(e)}", "ERROR")
        return None, None

# Excluir blob do Azure Storage
def excluir_blob(blob_name):
    """Exclui um blob do Azure Storage Container"""
    if not blob_name:
        log_debug("Nome do blob vazio, nada para excluir", "WARNING")
        return False
    
    try:
        log_debug(f"Excluindo blob: {blob_name}")
        
        # Conectar ao Blob Storage
        blob_service = BlobServiceClient.from_connection_string(blobConnectionString)
        container_client = blob_service.get_container_client(blobContainerName)
        blob_client = container_client.get_blob_client(blob_name)
        
        # Verificar se o blob existe
        if not blob_client.exists():
            log_debug(f"Blob não encontrado: {blob_name}", "WARNING")
            return False
        
        # Excluir o blob
        blob_client.delete_blob()
        log_debug(f"Blob excluído com sucesso: {blob_name}", "SUCCESS")
        return True
    except Exception as e:
        log_debug(f"Erro ao excluir blob: {str(e)}", "ERROR")
        return False

# FUNÇÃO COMPLETAMENTE REVISADA: Upload para Azure Blob
def upload_imagem(file):
    """Upload de arquivo para o Azure Blob Storage com validação robusta"""
    # Validar entrada
    if file is None:
        log_debug("Nenhum arquivo fornecido para upload", "WARNING")
        return None, None
    
    progress_placeholder = st.empty()
    status_text = st.empty()
    
    try:
        # Verificar tamanho do arquivo
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size == 0:
            status_text.error("Arquivo vazio selecionado")
            return None, None
            
        # Mostrar informações do arquivo
        status_text.info(f"Processando: {file.name} ({file_size/1024:.1f} KB)")
        log_debug(f"Iniciando upload de: {file.name} ({file_size/1024:.1f} KB)")
        
        # Mostrar progress bar
        progress_bar = progress_placeholder.progress(0)
        
        # Sanitizar nome do arquivo
        sanitized_filename = sanitizar_nome_arquivo(file.name)
        log_debug(f"Nome sanitizado: {sanitized_filename}")
        
        # Gerar nome único para evitar colisões
        timestamp = int(time.time())
        random_suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
        unique_filename = f"{timestamp}-{random_suffix}-{sanitized_filename}"
        log_debug(f"Nome único: {unique_filename}")
        
        # Atualizar progresso - 20%
        progress_bar.progress(20)
        status_text.info("Comprimindo imagem...")
        
        # Comprimir imagem
        compressed_file, formato = comprimir_imagem(file, max_size_kb=1024)
        if compressed_file is None or formato is None:
            status_text.error("Falha ao comprimir imagem - formato incompatível")
            progress_placeholder.empty()
            return None, None
            
        # Atualizar progresso - 50%
        progress_bar.progress(50)
        status_text.info("Enviando para Azure Blob Storage...")
        
        try:
            # Conectar ao Azure Blob Storage e fazer upload
            blob_service = BlobServiceClient.from_connection_string(blobConnectionString)
            container_client = blob_service.get_container_client(blobContainerName)
            blob_client = container_client.get_blob_client(unique_filename)
            
            # Upload com timeout explícito
            start_time = time.time()
            blob_client.upload_blob(compressed_file, overwrite=True, timeout=60)
            upload_time = time.time() - start_time
            
            # Construir URL do blob
            blob_url = f"https://{blobAccountName}.blob.core.windows.net/{blobContainerName}/{unique_filename}"
            
            # Finalizar progresso
            progress_bar.progress(100)
            status_text.success(f"Upload concluído em {upload_time:.2f} segundos")
            time.sleep(0.5)  # Breve pausa para mostrar a mensagem de sucesso
            
            # Limpar status
            progress_placeholder.empty()
            status_text.empty()
            
            log_debug(f"Upload concluído: {blob_url}", "SUCCESS")
            
            return blob_url, unique_filename
            
        except Exception as e:
            progress_bar.progress(100)
            status_text.error(f"Erro na conexão com Azure: {str(e)}")
            log_debug(f"Erro no upload para Azure: {str(e)}", "ERROR")
            time.sleep(1)
            progress_placeholder.empty()
            status_text.empty()
            return None, None
            
    except Exception as e:
        if status_text:
            status_text.error(f"Erro no processamento: {str(e)}")
        if progress_placeholder:
            progress_placeholder.empty()
        log_debug(f"Erro geral no upload: {str(e)}", "ERROR")
        return None, None

# FUNÇÃO MELHORADA: Inserir produto no banco com verificação de duplicidade
def inserir_produto(nome, descricao, preco, imagem_url, imagem_blob_name, categoria_id=1, estoque=0):
    """Insere um produto no banco de dados com validações de duplicidade"""
    try:
        log_debug(f"Iniciando inserção do produto: {nome}")
        
        # Gerar um hash único para este produto
        produto_hash = f"{nome}_{imagem_blob_name}"
        
        # Verificar se foi inserido recentemente
        if produto_hash in st.session_state.produtos_enviados:
            log_debug(f"Produto já enviado recentemente: {nome}", "WARNING")
            return False, ["Este produto já foi cadastrado. Para inserir novamente, use outra imagem ou nome."]
        
        # Obter conexão ao banco de dados
        conn = get_connection(max_retries=3)
        if not conn:
            return False, ["Erro de conexão com o banco de dados."]
        
        cursor = conn.cursor()
        
        # Verificar duplicidade no banco
        try:
            # Verificar por nome e URL
            cursor.execute(
                "SELECT COUNT(*) FROM Produtos WHERE nome = ? AND imagem_url = ?",
                (nome, imagem_url)
            )
            count = cursor.fetchone()[0]
            if count > 0:
                conn.close()
                return False, ["Produto com mesmo nome e imagem já existe no banco de dados."]
            
            # Não verificamos duplicidade por nome e descrição para permitir produtos similares
            # apenas verificamos por nome+URL para evitar uploads idênticos
            
        except Exception as e:
            log_debug(f"Erro na verificação de duplicidade: {str(e)}", "WARNING")
            # Continua mesmo se falhar a verificação
        
        # Validar valores
        try:
            preco_float = float(preco)
            estoque_int = int(estoque)
        except ValueError:
            conn.close()
            return False, ["Valor de preço ou estoque inválido"]
        
        # Inserir o produto
        try:
            cursor.execute(
                """INSERT INTO Produtos 
                (nome, descricao, preco, imagem_url, imagem_blob_name, categoria_id, estoque) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (nome, descricao, preco_float, imagem_url, imagem_blob_name, categoria_id, estoque_int)
            )
            
            # Commit explícito
            conn.commit()
            
            # Registrar produto enviado
            st.session_state.produtos_enviados.add(produto_hash)
            
            log_debug(f"Produto '{nome}' inserido com sucesso (ID: {cursor.rowcount})", "SUCCESS")
            
            return True, ["Produto cadastrado com sucesso!"]
            
        except Exception as e:
            try:
                conn.rollback()
            except:
                pass
            log_debug(f"Erro ao inserir produto: {str(e)}", "ERROR")
            return False, [f"Erro ao salvar produto: {str(e)}"]
            
        finally:
            try:
                cursor.close()
                conn.close()
            except:
                pass
                
    except Exception as e:
        log_debug(f"Erro geral na inserção: {str(e)}", "ERROR")
        return False, [f"Erro: {str(e)}"]

# FUNÇÃO CORRIGIDA: Excluir produto do banco e blob storage
def excluir_produto(id_produto):
    """
    Exclui um produto do banco de dados e sua imagem do Azure Blob Storage
    """
    log_debug(f"Iniciando exclusão do produto ID: {id_produto}", "INFO")
    
    try:
        # 1. Conectar ao banco de dados
        conn = get_connection(max_retries=3)
        if not conn:
            return False, "Erro de conexão com o banco de dados"
        
        cursor = None
        
        try:
            cursor = conn.cursor()
            
            # 2. Verificar se o produto existe e obter dados da imagem
            cursor.execute("""
                SELECT nome, imagem_url, imagem_blob_name 
                FROM Produtos 
                WHERE id = ?
            """, (id_produto,))
            
            resultado = cursor.fetchone()
            if not resultado:
                conn.close()
                return False, "Produto não encontrado"
            
            nome_produto, imagem_url, imagem_blob_name = resultado
            log_debug(f"Produto encontrado: '{nome_produto}' (ID: {id_produto})", "INFO")
            
            # 3. Se não temos o nome do blob armazenado, tentar extrair da URL
            if not imagem_blob_name and imagem_url:
                imagem_blob_name = extrair_nome_blob_da_url(imagem_url)
                log_debug(f"Nome do blob extraído da URL: {imagem_blob_name}")
            
            # 4. Excluir registro do banco de dados
            cursor.execute("DELETE FROM Produtos WHERE id = ?", (id_produto,))
            registros_afetados = cursor.rowcount
            
            if registros_afetados == 0:
                conn.rollback()
                log_debug("Nenhum registro afetado pela exclusão", "WARNING")
                return False, "Erro: Nenhum registro foi excluído"
            
            # IMPORTANTE: Commit imediato para confirmar a exclusão do banco
            conn.commit()
            log_debug(f"Produto '{nome_produto}' excluído do banco de dados", "SUCCESS")
            
            # 5. Fechar a conexão com o banco antes de prosseguir
            cursor.close()
            conn.close()
            cursor = None
            conn = None
            
            # 6. Excluir a imagem do Azure Blob Storage
            if imagem_blob_name:
                blob_excluido = excluir_blob(imagem_blob_name)
                if blob_excluido:
                    log_debug(f"Imagem excluída do Azure Blob Storage: {imagem_blob_name}", "SUCCESS")
                else:
                    log_debug(f"Aviso: A imagem não pôde ser excluída do Azure Storage: {imagem_blob_name}", "WARNING")
                    return True, f"Produto excluído, mas a imagem não pôde ser removida do storage"
            else:
                log_debug("Sem imagem para excluir ou nome do blob não encontrado", "INFO")
            
            # 7. Remover o hash do produto da lista de enviados
            produto_hash = f"{nome_produto}_{imagem_blob_name}"
            if produto_hash in st.session_state.produtos_enviados:
                st.session_state.produtos_enviados.remove(produto_hash)
            
            return True, f"Produto '{nome_produto}' excluído com sucesso!"
            
        except Exception as e:
            log_debug(f"Erro durante a exclusão: {str(e)}", "ERROR")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            return False, f"Falha ao excluir produto: {str(e)}"
            
        finally:
            # Garantir que recursos sejam liberados
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            if conn:
                try:
                    conn.close()
                except:
                    pass
            
    except Exception as e:
        log_debug(f"ERRO CRÍTICO AO EXCLUIR PRODUTO: {str(e)}", "ERROR")
        return False, f"Erro ao excluir produto: {str(e)}"

# Listar produtos
def listar_produtos(filtro=None, pagina=1, por_pagina=10):
    try:
        log_debug("Listando produtos do banco de dados")
        conn = get_connection()
        if not conn:
            log_debug("Falha ao obter conexão com o banco de dados", "ERROR")
            return pd.DataFrame(), 0, 0
        
        # Construir a consulta SQL base
        query_base = """
        SELECT p.*, c.nome as categoria_nome
        FROM Produtos p
        LEFT JOIN Categorias c ON p.categoria_id = c.id
        WHERE 1=1
        """
        
        params = []
        
        # Adicionar filtro por texto
        if filtro:
            query_base += " AND (p.nome LIKE ? OR p.descricao LIKE ?)"
            params.extend([f'%{filtro}%', f'%{filtro}%'])
        
        try:
            # Contar total de produtos
            query_count = f"SELECT COUNT(*) as total FROM Produtos p WHERE 1=1"
            if filtro:
                query_count += " AND (p.nome LIKE ? OR p.descricao LIKE ?)"
                
            cursor = conn.cursor()
            cursor.execute(query_count, params if filtro else [])
            total_produtos = cursor.fetchone()[0]
            
            # Calcular total de páginas
            total_paginas = (total_produtos + por_pagina - 1) // por_pagina if total_produtos > 0 else 1
            
            # Ajustar página atual
            pagina = max(1, min(pagina, total_paginas if total_paginas > 0 else 1))
            
            # Adicionar ordenação e paginação
            query_base += f" ORDER BY p.id DESC OFFSET {(pagina - 1) * por_pagina} ROWS FETCH NEXT {por_pagina} ROWS ONLY"
            
            # Executar consulta final
            cursor = conn.cursor()
            cursor.execute(query_base, params)
            columns = [column[0] for column in cursor.description]
            data = cursor.fetchall()
            
            # Converter para DataFrame
            df = pd.DataFrame.from_records(data, columns=columns) if data else pd.DataFrame()
            
            conn.close()
            log_debug(f"Encontrados {total_produtos} produtos (página {pagina} de {total_paginas})")
            return df, total_produtos, total_paginas
        except Exception as e:
            log_debug(f"Erro ao executar consulta: {str(e)}", "ERROR")
            conn.close()
            return pd.DataFrame(), 0, 0
    except Exception as e:
        log_debug(f"ERRO AO LISTAR PRODUTOS: {str(e)}", "ERROR")
        return pd.DataFrame(), 0, 0

# FUNÇÃO DE CONFIRMAÇÃO DE EXCLUSÃO: Separada para lidar com a lógica de estado
def confirmar_exclusao(produto_id, produto_nome):
    """Preparar interface de confirmação de exclusão"""
    st.session_state.produto_a_excluir = produto_id
    st.session_state.nome_produto_a_excluir = produto_nome
    st.session_state.exclusao_confirmada = False
    st.session_state.exclusao_em_andamento = False
    log_debug(f"Solicitada confirmação de exclusão do produto {produto_id}: {produto_nome}", "INFO")
    st.rerun()

# FUNÇÃO PARA PROCESSAR A EXCLUSÃO
def processar_exclusao():
    """Processa a exclusão do produto após confirmação"""
    if not st.session_state.produto_a_excluir:
        return
    
    if st.session_state.exclusao_em_andamento:
        return
    
    st.session_state.exclusao_em_andamento = True
    produto_id = st.session_state.produto_a_excluir
    
    log_debug(f"Processando exclusão do produto {produto_id}", "INFO")
    
    # Executar exclusão
    resultado, mensagem = excluir_produto(produto_id)
    
    # Resetar estado
    st.session_state.produto_a_excluir = None
    st.session_state.nome_produto_a_excluir = None
    st.session_state.exclusao_confirmada = False
    st.session_state.exclusao_em_andamento = False
    
    # Retornar resultado
    return resultado, mensagem

# Interface principal
try:
    # Sidebar com opções
    with st.sidebar:
        st.subheader("Opções")
        
        if st.button("Verificar Conexão"):
            resultado, mensagem = verificar_conexao()
            if resultado:
                st.success(mensagem)
            else:
                st.error(mensagem)
        
        if st.button("Limpar Cache"):
            limpar_cache_produtos()
            st.session_state.processando_upload = False
            st.success("Cache limpo com sucesso!")
            time.sleep(1)
            st.rerun()

    # Inicializar banco
    inicializar_banco_de_dados()

    # Interface principal
    st.title("🛒 Cadastro de Produtos com Imagens - Gilson Silva")

    # Abas para cadastro e listagem
    tab_cadastro, tab_listagem = st.tabs(["📝 Cadastrar Produto", "📋 Listar Produtos"])

    # Aba de cadastro
    with tab_cadastro:
        st.header("Novo Produto")
        
        # FORMULÁRIO CORRIGIDO - SEM USAR SESSION STATE PARA CONTROLE
        with st.form(key="cadastro_produto_form", clear_on_submit=True):
            nome = st.text_input("Nome do Produto", key="nome_produto")
            descricao = st.text_area("Descrição do Produto", key="descricao_produto")
            
            col1, col2 = st.columns(2)
            with col1:
                preco = st.number_input("Preço do Produto (R$)", min_value=0.0, step=0.01, format="%.2f", key="preco_produto")
            with col2:
                estoque = st.number_input("Estoque", min_value=0, value=1, step=1, key="estoque_produto")
            
            # Upload de imagem
            uploaded_file = st.file_uploader("Imagem do produto", type=["jpg", "jpeg", "png"], key="imagem_produto")
            
            # Botão de envio
            submit_button = st.form_submit_button("💾 Salvar Produto", use_container_width=True)
        
        # Lógica de processamento FORA do formulário para melhor controle
        if submit_button:
            # Validações básicas
            if not nome:
                st.warning("⚠️ Informe o nome do produto.")
            elif not uploaded_file:
                st.warning("⚠️ Selecione uma imagem para o produto.")
            else:
                # Verificar se estamos processando outro upload
                if st.session_state.processando_upload:
                    st.warning("⚠️ Já existe um upload em andamento, aguarde...")
                else:
                    # Marcar que estamos processando
                    st.session_state.processando_upload = True
                    
                    # Mostrar detalhes do arquivo
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        try:
                            st.image(uploaded_file, caption="Imagem selecionada", width=200)
                        except:
                            st.error("Não foi possível mostrar a imagem")
                    with col2:
                        st.info(f"Processando: {uploaded_file.name}")
                    
                    # Fazer upload da imagem
                    with st.spinner("Enviando imagem para Azure Blob Storage..."):
                        imagem_url, imagem_blob_name = upload_imagem(uploaded_file)
                    
                    # Verificar resultado do upload
                    if not imagem_url or not imagem_blob_name:
                        st.error("❌ Falha ao fazer upload da imagem. Verifique o arquivo e tente novamente.")
                        st.session_state.processando_upload = False
                    else:
                        # Imagem foi carregada com sucesso, salvar o produto
                        with st.spinner("Salvando produto no banco de dados..."):
                            resultado, mensagens = inserir_produto(
                                nome, descricao, preco, imagem_url, imagem_blob_name, 1, estoque
                            )
                        
                        # Mostrar resultado
                        if resultado:
                            st.success(f"✅ Produto '{nome}' cadastrado com sucesso!")
                            st.session_state.ultimo_upload_timestamp = time.time()
                        else:
                            erro_msg = mensagens[0] if mensagens else "Erro desconhecido"
                            st.error(f"❌ {erro_msg}")
                            
                            # Se falhou ao salvar no banco, excluir o blob
                            if imagem_blob_name:
                                with st.spinner("Removendo imagem enviada..."):
                                    excluir_blob(imagem_blob_name)
                        
                        # Limpar flag de processamento
                        st.session_state.processando_upload = False

    # Aba de listagem
    with tab_listagem:
        st.header("Produtos Cadastrados")
        
        # Botão para recarregar a lista de produtos
        col_refresh, col_empty = st.columns([1, 3])
        with col_refresh:
            if st.button("🔄 Recarregar Lista", use_container_width=True, type="primary"):
                # Limpa o cache e recarrega a página
                st.session_state.produto_a_excluir = None
                st.session_state.pagina_atual = 1
                st.rerun()
        
        # Filtro simples
        filtro = st.text_input("Buscar produto por nome ou descrição")
        
        # Listar produtos com tratamento de erros
        try:
            with st.spinner("Carregando produtos..."):
                produtos, total_produtos, total_paginas = listar_produtos(
                    filtro=filtro,
                    pagina=st.session_state.pagina_atual
                )
        except Exception as e:
            st.error(f"Erro ao carregar produtos: {str(e)}")
            produtos = pd.DataFrame()
            total_produtos = 0
            total_paginas = 0
        
        # Mostrar total encontrado
        st.write(f"Total de produtos: {total_produtos}")
        
        # Processar exclusão se confirmada
        if st.session_state.exclusao_confirmada and st.session_state.produto_a_excluir:
            with st.spinner("Excluindo produto..."):
                resultado, mensagem = processar_exclusao()
            
            if resultado:
                st.success(mensagem)
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(mensagem)
        
        # Modal de confirmação de exclusão (se um produto foi selecionado)
        if st.session_state.produto_a_excluir and not st.session_state.exclusao_confirmada:
            with st.container():
                st.warning(f"Confirma a exclusão do produto '{st.session_state.nome_produto_a_excluir}'?")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✓ Sim, excluir", type="primary"):
                        st.session_state.exclusao_confirmada = True
                        st.rerun()
                with col2:
                    if st.button("✗ Não, cancelar"):
                        st.session_state.produto_a_excluir = None
                        st.session_state.nome_produto_a_excluir = None
                        st.rerun()
        
        # Exibir produtos em formato de cards/grid
        if produtos.empty:
            st.info("Nenhum produto encontrado.")
        else:
            # Definir número de colunas por linha no grid
            num_colunas = 3
            
            # Criar listas de produtos em grupos para exibição em grid
            produtos_grupos = [produtos.iloc[i:i+num_colunas] for i in range(0, len(produtos), num_colunas)]
            
            # Para cada grupo de produtos, criar uma linha
            for grupo in produtos_grupos:
                # Criar colunas para cada produto no grupo
                cols = st.columns(num_colunas)
                
                # Para cada produto no grupo, exibir em sua respectiva coluna
                for i, (_, produto) in enumerate(grupo.iterrows()):
                    with cols[i]:
                        # Card do produto com estilo
                        with st.container():
                            # Área da imagem
                            if produto['imagem_url']:
                                try:
                                    st.image(produto['imagem_url'], use_column_width=True)
                                except:
                                    st.warning("Imagem não disponível")
                            else:
                                st.warning("Sem imagem")
                            
                            # Título do produto
                            st.markdown(f"### {produto['nome']}")
                            
                            # Descrição
                            if pd.notna(produto['descricao']) and produto['descricao'].strip():
                                st.markdown(f"**Descrição:** {produto['descricao']}")
                            
                            # Preço com formatação destacada
                            st.markdown(f"**Preço:** R$ {float(produto['preco']):.2f}")
                            
                            # Informações adicionais
                            st.caption(f"Estoque: {produto['estoque']} unidades")
                            
                            # Botão de ação
                            if st.button("🗑️ Excluir", key=f"del_{produto['id']}",
                                       help="Excluir este produto"):
                                confirmar_exclusao(int(produto['id']), produto['nome'])
            
            # Controles de paginação
            if total_paginas > 1:
                st.divider()
                col1, col2, col3 = st.columns([1, 2, 1])
                
                with col1:
                    if st.session_state.pagina_atual > 1:
                        if st.button("← Anterior", use_container_width=True):
                            st.session_state.pagina_atual -= 1
                            st.rerun()
                
                with col2:
                    st.markdown(f"<div style='text-align: center'>Página {st.session_state.pagina_atual} de {total_paginas}</div>", unsafe_allow_html=True)
                
                with col3:
                    if st.session_state.pagina_atual < total_paginas:
                        if st.button("Próxima →", use_container_width=True):
                            st.session_state.pagina_atual += 1
                            st.rerun()

    # Mostrar versão no rodapé
    st.sidebar.caption("Versão: 3.1.0")
    st.sidebar.caption(f"Data: {datetime.now().strftime('%d/%m/%Y')}")
    st.sidebar.caption("Status: Corrigido com Grid ✅")

except Exception as e:
    st.error(f"Erro inesperado: {str(e)}")
    st.exception(e)  # Mostrar detalhes completos do erro para diagnóstico
    st.info("Tente reiniciar a aplicação ou entre em contato com o suporte.")
