import apiClient from './client';
import type {
  LoginRequest,
  TokenResponse,
  LogoutRequest,
  MeResponse,
  CreateUserRequest,
  UserResponse,
} from '../types';

export const authApi = {
  login: (data: LoginRequest) =>
    apiClient.post<TokenResponse>('/api/v1/auth/login', data).then((r) => r.data),

  logout: (data: LogoutRequest) =>
    apiClient.post<void>('/api/v1/auth/logout', data).then((r) => r.data),

  me: () =>
    apiClient.get<MeResponse>('/api/v1/auth/me').then((r) => r.data),

  createUser: (data: CreateUserRequest) =>
    apiClient.post<UserResponse>('/api/v1/auth/users', data).then((r) => r.data),

  listUsers: (search?: string) =>
    apiClient
      .get<UserResponse[]>('/api/v1/auth/users', { params: search ? { search } : {} })
      .then((r) => r.data),
};
