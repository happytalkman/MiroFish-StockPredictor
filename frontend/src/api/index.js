import axios from 'axios'

// axios 인스턴스 생성
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '', // Vite 프록시(/api → 5001) 사용
  timeout: 300000, // 5분 타임아웃 (온톨로지 생성에 시간이 오래 걸릴 수 있음)
  headers: {
    'Content-Type': 'application/json'
  }
})

// 요청 인터셉터
service.interceptors.request.use(
  config => {
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 응답 인터셉터 (오류 허용 재시도 메커니즘)
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 반환된 상태 코드가 success가 아니면 오류 발생
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    // 공개 주소(stock.webig.ai)에서 로그인이 풀렸으면 접속 코드 화면으로 이동
    const r = error?.response
    if (r?.status === 401 && r.data?.login && typeof window !== 'undefined') {
      const next = window.location.pathname + window.location.search
      window.location.href = `${r.data.login}?next=${encodeURIComponent(next)}`
      return new Promise(() => {})
    }
    console.error('Response error:', error)
    
    // 타임아웃 처리
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 네트워크 오류 처리
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }
    
    return Promise.reject(error)
  }
)

// 재시도 기능이 있는 요청 함수
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
