-- Staff Finder — Schema Supabase
-- Correr en el SQL Editor de tu proyecto Supabase

create extension if not exists "uuid-ossp";

-- Búsquedas (un proceso de selección)
create table searches (
  id uuid default uuid_generate_v4() primary key,
  name text not null,
  company text not null,
  is_active boolean default true,
  created_at timestamptz default now()
);

-- Puestos dentro de cada búsqueda
create table positions (
  id uuid default uuid_generate_v4() primary key,
  search_id uuid references searches(id) on delete cascade,
  title text not null,
  requirements text not null,
  created_at timestamptz default now()
);

-- Candidatos
create table candidates (
  id uuid default uuid_generate_v4() primary key,
  search_id uuid references searches(id) on delete cascade,
  name text not null,
  email text,
  bio text,
  pdf_url text,
  pdf_text text,
  gmail_message_id text unique,
  position text,
  category text default 'solo',           -- 'solo' | 'couple'
  couple_partner_id uuid references candidates(id),
  status text default 'nuevo',            -- nuevo | revisado | preseleccionado | entrevista | contratado | descartado
  ai_score integer,
  ai_summary text,
  ai_strengths jsonb default '[]',
  ai_gaps jsonb default '[]',
  created_at timestamptz default now()
);

-- Notas del equipo sobre cada candidato
create table notes (
  id uuid default uuid_generate_v4() primary key,
  candidate_id uuid references candidates(id) on delete cascade,
  author_email text,
  text text not null,
  created_at timestamptz default now()
);

-- Row Level Security — solo usuarios autenticados
alter table searches enable row level security;
alter table positions enable row level security;
alter table candidates enable row level security;
alter table notes enable row level security;

create policy "auth_searches" on searches for all to authenticated using (true) with check (true);
create policy "auth_positions" on positions for all to authenticated using (true) with check (true);
create policy "auth_candidates" on candidates for all to authenticated using (true) with check (true);
create policy "auth_notes" on notes for all to authenticated using (true) with check (true);

-- Storage bucket para CVs (correr por separado si el bucket no existe)
-- insert into storage.buckets (id, name, public) values ('cvs', 'cvs', true);
-- create policy "public_read_cvs" on storage.objects for select using (bucket_id = 'cvs');
-- create policy "auth_write_cvs" on storage.objects for insert to authenticated with check (bucket_id = 'cvs');
